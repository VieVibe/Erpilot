import hashlib
import json
import re

import pytest
from agent_core.approval import AutoDenyGate
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.db import make_engine
from erp_store.seed import seed_database
from evals.model import CaseResult
from evals.task_cases import CUSTOMER_A, DRAFT_NOTE, TASK_CASES
from evals.task_reference_cases import REFERENCE_CASES, REFERENCE_SCORER_VERSION
from evals.task_runtime import run_task_case
from mcp_erp import build_agent_tools_async


def reference_client(fault=None):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[index]["content"]
        names = {call["id"]: call["function"]["name"]
                 for m in messages[index + 1:] for call in m.get("tool_calls", [])}
        outputs = {names[m["tool_call_id"]]: json.loads(m["content"])
                   for m in messages[index + 1:] if m["role"] == "tool"}

        def say(text):
            return sse_response([chunk(delta={"content": text}), chunk(usage=USAGE)])

        def call(name, args):
            return sse_response(tool_call_chunks(
                f"ref-{len(requests)}", name, json.dumps(args, ensure_ascii=False),
            ))

        if "保存一份报价草稿" in question:
            if "prepare_order_draft" in outputs:
                return say("草稿已保存，库存为预计测算，未预留或扣减。")
            return call("prepare_order_draft", {
                "customer": CUSTOMER_A, "note": DRAFT_NOTE,
                "items": [{"sku": s, "quantity": q} for s, q in zip(
                    re.findall(r"[A-E]\d{4}", question), (2, 3), strict=True,
                )],
            })
        if "get_order_task" not in outputs:
            return call("get_order_task", {})
        draft = outputs["get_order_task"]["draft"]
        if "revise_order_draft_quantity" in outputs:
            return say("已按目标 SKU 保存新草稿，库存为预计值，未预留或扣减。")
        if "库存前后" in question:
            return say("库存已经扣减并占用。" if fault == "claim_reserved"
                       else "库存前后是报价时测算的预计值，保存草稿不预留、不扣减库存。")
        if "50" not in question:
            return say("当前草稿明细已查询，库存为预计值，不预留或扣减库存。")
        ambiguous = "把第二个改成" in question and "当前版本" not in question
        if ambiguous and fault != "early_write":
            return say("新旧版本顺序不同，请确认要修改哪一个 SKU：" +
                       "、".join(line["sku"] for line in draft["items"]))
        skus = re.findall(r"[A-E]\d{4}", question)
        target = skus[0] if skus else draft["items"][1]["sku"]
        if fault == "wrong_sku" and skus:
            target = next(line["sku"] for line in draft["items"] if line["sku"] != target)
        item = next(i for i, line in enumerate(draft["items"], 1) if line["sku"] == target)
        args = {"draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                "item_number": item, "quantity": 50}
        if fault != "missing_guard":
            args["expected_sku"] = target
        return call("revise_order_draft_quantity", args)

    return make_client(handler, max_retries=0)


@pytest.fixture
def reference_db(tmp_path):
    path = tmp_path / "reference.db"
    seed_database(path, n_products=60, n_orders=80)
    engine = make_engine(path)
    yield path, engine
    engine.dispose()


async def run_reference(case, reference_db, tmp_path, fault=None):
    path, engine = reference_db
    session_id = f"eval:{case.id}"
    tools = await build_agent_tools_async(
        path, writes=True, session_id=session_id, approval_gate=AutoDenyGate(),
    )
    return await run_task_case(
        case, client=reference_client(fault), tools=tools, engine=engine,
        resolved={}, trace_dir=tmp_path / "traces", session_id=session_id,
    )


@pytest.mark.parametrize("case", REFERENCE_CASES, ids=lambda c: c.id)
async def test_reference_contract_uses_real_tools_and_preserves_per_turn_evidence(
    case, reference_db, tmp_path,
):
    result, trace = await run_reference(case, reference_db, tmp_path)
    assert result.passed, result.failed_checks
    assert result.completed and trace.is_file()
    assert len(result.turn_results) == len(case.turns)
    assert all(turn.state_evidence and turn.visible_text for turn in result.turn_results)
    if case.id.endswith("confirmed"):
        clarify = result.turn_results[1]
        assert clarify.state_evidence["before_sha256"] == clarify.state_evidence["after_sha256"]
        last = result.turn_results[-1]
        payload = last.tool_results[-1].content
        assert [line["quantity"] for line in payload["items"]] == [50, 3]


@pytest.mark.parametrize("case_index,fault,marker", [
    (1, "early_write", "b4_write_attempted"),
    (1, "wrong_sku", "b4_revision_count"),
    (0, "missing_guard", "b4_reference_sku_guard"),
    (2, "claim_reserved", "must_mention_any_groups"),
])
async def test_reference_contract_rejects_early_edits_wrong_targets_and_false_inventory(
    case_index, fault, marker, reference_db, tmp_path,
):
    result, _ = await run_reference(REFERENCE_CASES[case_index], reference_db, tmp_path, fault)
    assert not result.passed
    assert any(marker in failure for failure in result.failed_checks), result.failed_checks
    if fault == "wrong_sku":
        failed_turn = result.turn_results[-1]
        edit = next(t for t in failed_turn.tool_results if t.name == "revise_order_draft_quantity")
        assert not edit.ok and edit.content["error"]["type"] == "argument_constraint"
        state = failed_turn.state_evidence
        assert state["before_sha256"] == state["after_sha256"]
        assert state["drafts_before"] == state["drafts_after"]


def test_legacy_b4_case_fingerprint_is_unchanged():
    digest = hashlib.sha256("\n".join(c.model_dump_json() for c in TASK_CASES).encode()).hexdigest()
    assert digest == "8ae7beb6c65a7da80e6ca77df2e319b566b23668ee349164c3e4fac902679675"


def test_projection_explanation_requires_structured_quote_evidence():
    from evals.model import CaseCategory, ToolResult
    from evals.task_reference_cases import check_reference_turn

    result = CaseResult(case_id="inventory", category=CaseCategory.MULTI, passed=True,
                        tool_results=[ToolResult(
                            call_id="c", name="get_order_task", arguments={}, ok=True,
                            content={"draft": {"draft_id": "d", "inventory_effect": "executed"}},
                        )])
    failed = check_reference_turn(REFERENCE_CASES[2].turns[1], result, resolved={})
    assert any("b4_reference_projection" in item for item in failed)


async def test_reference_suite_records_its_own_version_and_seven_turns(tmp_path):
    from evals.baseline import run_baseline

    results, path = await run_baseline(
        REFERENCE_CASES, client=reference_client(),
        report_dir=tmp_path / "reports", trace_dir=tmp_path / "traces",
    )
    report = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert all(r.passed for r in results)
    assert report["suite"]["b4_reference_scorer_version"] == REFERENCE_SCORER_VERSION
    assert report["suite"]["turns_planned"] == report["suite"]["turns_executed"] == 7
    assert report["suite"]["complete"]


def test_cli_selects_reference_suite_without_changing_legacy_cases(monkeypatch, tmp_path):
    import evals.__main__ as cli

    monkeypatch.setattr(cli, "find_dotenv", lambda *args: None)

    monkeypatch.setattr(cli.LLMConfig, "from_env", lambda: reference_client().config)
    monkeypatch.setattr(cli, "AsyncOpenAI", lambda **kwargs: object())

    async def capture(cases, **kwargs):
        assert [c.id for c in cases] == [REFERENCE_CASES[1].id]
        assert kwargs["client"].request_timeout == 30
        return [CaseResult(case_id=cases[0].id, category=cases[0].category, passed=True)], tmp_path

    monkeypatch.setattr(cli, "run_baseline", capture)
    monkeypatch.setattr("sys.argv", ["evals", "--suite", "b4-reference",
                                     "--case", REFERENCE_CASES[1].id])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0

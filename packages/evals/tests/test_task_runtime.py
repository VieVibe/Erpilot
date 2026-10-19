"""真实 MCP + HTTP mock 的多轮评测；模型准确率须另跑真实供应商。"""

import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from agent_core.approval import AutoDenyGate
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.db import make_engine
from erp_store.drafts import ErpDrafts
from erp_store.seed import seed_database
from evals.billing import BudgetedClient, CurrencyBudget, EndpointRate
from evals.model import CaseCategory, CaseResult
from evals.task_cases import (
    CUSTOMER_A,
    DRAFT_NOTE,
    TASK_CASES,
    check_task_turn,
)
from evals.task_runtime import prepare_task_fixture, run_task_case, snapshot_all
from mcp_erp import build_agent_tools_async


def task_client(*, wrong_item=False, usage=USAGE):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[index]["content"]
        outputs = {}
        names = {m["tool_calls"][0]["id"]: m["tool_calls"][0]["function"]["name"]
                 for m in messages[index + 1:] if m.get("tool_calls")}
        for message in messages[index + 1:]:
            if message["role"] == "tool":
                outputs[names[message["tool_call_id"]]] = json.loads(message["content"])

        def call(name, args):
            return sse_response(tool_call_chunks(
                f"call-{len(requests)}", name, json.dumps(args, ensure_ascii=False), usage=usage,
            ))

        def say(text):
            chunks = [chunk(delta={"content": text})]
            if usage is not None:
                chunks.append(chunk(usage=usage))
            return sse_response(chunks)

        if "保存一份报价草稿" in question:
            if "prepare_order_draft" in outputs:
                return say("已保存草稿，未提交订单。")
            skus = re.findall(r"[A-E]\d{4}", question)
            return call("prepare_order_draft", {
                "customer": CUSTOMER_A, "note": DRAFT_NOTE,
                "items": [{"sku": sku, "quantity": qty}
                          for sku, qty in zip(skus, (2, 3), strict=True)],
            })
        if "get_order_task" not in outputs:
            return call("get_order_task", {})
        task = outputs["get_order_task"]
        if "改成" not in question:
            return say("已查询草稿；如有多份，请明确选择哪一份。")
        if task["status"] == "none":
            return say("没有当前会话草稿，请提供客户、商品等信息。")
        if task["status"] == "ambiguous":
            candidate = next((c for c in task["candidates"] if c["customer"] in question), None)
            if candidate is None:
                return say("有多份草稿，请明确选择哪一份。")
            if "get_order_draft" not in outputs:
                return call("get_order_draft", {"draft_id": candidate["draft_id"]})
            draft = outputs["get_order_draft"]
        else:
            draft = task["draft"]
        if "revise_order_draft_quantity" in outputs:
            return say("草稿数量已更新，其他条件保留，未提交订单。")
        item = 1 if "第一个" in question or wrong_item else 2
        return call("revise_order_draft_quantity", {
            "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
            "item_number": item, "quantity": 12 if "12" in question else 50,
        })

    return make_client(handler, max_retries=0), requests


@pytest.fixture
def task_db(tmp_path):
    path = tmp_path / "b4.db"
    seed_database(path, n_products=60, n_orders=80)
    engine = make_engine(path)
    yield path, engine
    engine.dispose()


async def run_task(case, task_db, tmp_path, client, budget=None):
    path, engine = task_db
    session_id = f"eval:{case.id}"
    tools = await build_agent_tools_async(
        path, writes=True, approval_gate=AutoDenyGate(), session_id=session_id,
    )
    assert {"submit_order_draft", "cancel_order", "adjust_stock", "set_product_status"} <= {
        t.name for t in tools
    }
    return await run_task_case(
        case, client=client, tools=tools, engine=engine, resolved={},
        trace_dir=tmp_path / "traces", session_id=session_id, budget=budget,
    )


@pytest.mark.parametrize("case", TASK_CASES, ids=lambda c: c.id)
async def test_task_scenarios_use_real_mcp_and_per_turn_evidence(case, task_db, tmp_path):
    client, requests = task_client()
    result, trace = await run_task(case, task_db, tmp_path, client)
    assert result.passed, result.failed_checks
    assert result.completed and trace.is_file()
    assert len(result.turn_results) == len(case.turns) == len(result.turn_traces)
    assert result.total_tokens == len(requests) * USAGE["total_tokens"]
    assert result.cost == pytest.approx(sum(t.cost for t in result.turn_results))
    for turn, path in zip(result.turn_results, result.turn_traces, strict=True):
        assert turn.state_evidence["before_sha256"]
        records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
        assert records[0]["type"] == "run_start" and records[-1]["type"] == "run_end"
    last = requests[-1]["messages"]
    user_count = sum(m["role"] == "user" for m in last)
    assert user_count == (1 if case.id == "b4-restored-task" else len(case.turns))


async def test_wrong_reference_fails_and_stops_before_third_turn(task_db, tmp_path):
    client, requests = task_client(wrong_item=True)
    result, _ = await run_task(TASK_CASES[1], task_db, tmp_path, client)
    assert not result.passed and not result.completed
    assert any("b4_constraints" in f for f in result.failed_checks)
    assert len(result.turn_results) == 2
    assert max(sum(m["role"] == "user" for m in r["messages"]) for r in requests) == 2


def _budget_client(client, *, max_requests=40):
    budget = CurrencyBudget(Decimal("0.1"), "USD", max_requests=max_requests)
    rate = EndpointRate(
        model=client.config.model, endpoint_host="llm.test", currency="USD",
        input_per_m=Decimal("0.045"), output_per_m=Decimal("0.18"), price_source="fixture",
    )
    return BudgetedClient(client, rate, budget), budget


async def test_budget_between_turns_preserves_first_turn_and_bills_once(task_db, tmp_path):
    raw, requests = task_client()
    client, budget = _budget_client(raw, max_requests=2)
    result, _ = await run_task(TASK_CASES[0], task_db, tmp_path, client, budget)
    assert not result.passed and not result.completed
    assert len(requests) == budget.requests == 2
    assert result.turn_results[0].passed
    assert result.turn_results[1].failed_checks[0].startswith("not_run:")
    assert Decimal(result.billing.estimated_amount) == budget.spent
    assert len(result.turn_traces) == 1


async def test_unknown_usage_stops_task_without_replaying_saved_draft(task_db, tmp_path):
    raw, requests = task_client(usage=None)
    client, budget = _budget_client(raw)
    result, _ = await run_task(TASK_CASES[0], task_db, tmp_path, client, budget)
    assert not result.passed and not result.completed
    assert budget.unknown_usage and len(requests) == budget.requests == 1
    assert result.billing.estimated_amount is None and not result.billing.complete
    assert len(result.turn_results) == 1
    own = [r for r in snapshot_all(task_db[1])["order_drafts"]
           if r["session_id"] == f"eval:{TASK_CASES[0].id}"]
    assert len(own) == 1  # 已保存的草稿不能因模型下一步熔断而重放。


@pytest.mark.parametrize("fault", ["foreign", "old_revision", "extra_draft", "business", "attempt"])
def test_oracle_rejects_unauthorized_state_and_calls(task_db, fault):
    case = TASK_CASES[4]
    session = f"eval:{case.id}"
    skus = prepare_task_fixture(case, task_db[1], session)
    before = snapshot_all(task_db[1])
    after = deepcopy(before)
    result = CaseResult(case_id="t", category=CaseCategory.MULTI, passed=True)
    row = after["order_drafts"][0]
    if fault == "foreign":
        row["session_id"] = "wrong-session"
    elif fault == "old_revision":
        row["snapshot"] = "{}"
    elif fault == "extra_draft":
        after["order_drafts"].append({**row, "draft_id": "extra"})
    elif fault == "business":
        after["stocks"][0]["quantity"] += 1
    else:
        result.tool_calls = ["submit_order_draft"]  # 即使被审批拒绝也判失败。
    assert check_task_turn(case.turns[0], result, before, after, session_id=session, skus=skus)


async def test_baseline_reports_scope_turns_billing_and_state_evidence(tmp_path):
    from evals.baseline import run_baseline

    raw, requests = task_client()
    client, budget = _budget_client(raw)
    results, path = await run_baseline(
        TASK_CASES[:1], client=client, currency_budget=budget,
        report_dir=tmp_path / "reports", trace_dir=tmp_path / "traces",
    )
    assert results[0].passed, results[0].failed_checks
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    suite = payload["suite"]
    assert suite["b4_scorer_version"] == "b4-1" and suite["complete"]
    assert suite["turns_executed"] == suite["turns_planned"] == 2
    assert suite["billing_budget"]["requests"] == len(requests)
    assert Decimal(results[0].billing.estimated_amount) == budget.spent
    assert len(payload["results"][0]["turn_results"]) == 2
    assert "多轮任务明细" in path.read_text(encoding="utf-8")
    from evals.gate import GatePolicy, evaluate_gate

    rules = GatePolicy(name="scripted-b4-integration", case_ids=[TASK_CASES[0].id],
                       case_turns={TASK_CASES[0].id: 2})
    decision = evaluate_gate(payload, rules)
    assert decision["passed"], decision["checks"]
    assert Decimal(decision["metrics"]["estimated_costs"]["USD"]) == budget.spent


async def test_single_turn_scoring_does_not_reuse_previous_visible_text(tmp_path):
    from evals.model import EvalCase
    from evals.runner import run_case

    client = make_client(lambda request: sse_response([chunk(delta={"content": "只有本轮文本"})]))
    conversation = [{"role": "system", "content": "规则"},
                    {"role": "assistant", "content": "旧轮次的关键词"}]
    result, _ = await run_case(
        EvalCase(id="t", category=CaseCategory.MULTI, question="继续", points="逐轮判分",
                 must_mention=["旧轮次的关键词"]),
        client=client, tools=[], resolved={}, trace_dir=tmp_path, conversation=conversation,
    )
    assert not result.passed and any("must_mention" in f for f in result.failed_checks)


@pytest.mark.parametrize("fault", ["customer", "note", "sku", "quantity", "revision", "session"])
def test_oracle_rejects_wrong_revision_target_and_lost_constraints(task_db, fault):
    case = TASK_CASES[4]
    session = f"eval:{case.id}"
    engine = task_db[1]
    skus = prepare_task_fixture(case, engine, session)
    before = snapshot_all(engine)
    drafts = ErpDrafts(engine)
    draft = drafts.task_state(session)["draft"]
    drafts.revise_quantity(session, draft["draft_id"], draft["revision"], 2, 50)
    after = snapshot_all(engine)
    result = CaseResult(case_id="t", category=CaseCategory.MULTI, passed=True)
    turn = case.turns[1]
    assert check_task_turn(turn, result, before, after, session_id=session, skus=skus) == []
    row = next(r for r in after["order_drafts"] if r["revision"] == 2)
    payload = json.loads(row["snapshot"])
    if fault in ("customer", "note"):
        payload[fault] = "错误内容"
    elif fault in ("sku", "quantity"):
        payload["items"][0][fault] = "wrong" if fault == "sku" else 49
    elif fault == "revision":
        row["revision"] = 3
    else:
        row["session_id"] = "b4-foreign"
    row["snapshot"] = json.dumps(payload)
    assert check_task_turn(turn, result, before, after, session_id=session, skus=skus)


async def test_partial_task_cannot_be_reported_as_complete(tmp_path):
    from evals.baseline import run_baseline

    raw, _ = task_client()
    client, budget = _budget_client(raw, max_requests=2)
    results, path = await run_baseline(
        TASK_CASES[:1], client=client, currency_budget=budget,
        report_dir=tmp_path / "reports", trace_dir=tmp_path / "traces",
    )
    report = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert not report["suite"]["complete"]
    assert report["suite"]["executed"] == 1  # 已执行首轮，不能算整个 case 未执行。
    assert report["suite"]["turns_executed"] == 1 < report["suite"]["turns_planned"]
    assert results[0].turn_results[0].state_evidence["drafts_after"]


def test_task_cli_selects_b4_without_changing_baseline(monkeypatch, tmp_path):
    import evals.__main__ as cli
    from evals.baseline import BASELINE_CASES

    baseline_ids = [c.id for c in BASELINE_CASES]
    config = make_client(lambda request: None).config
    monkeypatch.setattr(cli.LLMConfig, "from_env", lambda: config)
    monkeypatch.setattr(cli, "AsyncOpenAI", lambda **kwargs: object())

    async def capture(cases, **kwargs):
        assert [c.id for c in cases] == [TASK_CASES[0].id]
        assert len(cases[0].turns) == 2
        return [CaseResult(case_id=cases[0].id, category=cases[0].category, passed=True)], tmp_path

    monkeypatch.setattr(cli, "run_baseline", capture)
    monkeypatch.setattr("sys.argv", ["evals", "--suite", "b4", "--case", TASK_CASES[0].id])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert len(BASELINE_CASES) == 35 and [c.id for c in BASELINE_CASES] == baseline_ids

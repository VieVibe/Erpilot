import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from evals import context_workflow as cw
from evals.model import CaseCategory, CaseResult


class ScriptedClient:
    config = LLMConfig("offline-fixture", base_url="https://flashcoding.ai/v1")

    def __init__(self, *, unknown=False):
        self.requests = []
        self.unknown = unknown

    async def stream_chat(self, messages, tools=None):
        self.requests.append(deepcopy(messages))
        index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[index]["content"]
        turn = next(t for w in cw.WORKFLOWS for t in w.turns if t.question == question)
        names = {c["id"]: c["function"]["name"]
                 for m in messages[index + 1:] for c in m.get("tool_calls", [])}
        outputs = {names[m["tool_call_id"]]: json.loads(m["content"])
                   for m in messages[index + 1:] if m["role"] == "tool"}
        name = next((n for n in turn.tools if n not in outputs), None)
        args = {}
        if name == "prepare_order_draft":
            args = {"customer": turn.customer, "note": cw.NOTE,
                    "items": [{"sku": s, "quantity": q}
                              for s, q in zip(cw.SKUS[:2], (2, 3), strict=True)]}
        elif name == "get_stock":
            args = {"sku": cw.SKUS[0]}
        elif name == "list_products":
            args = {"status": "在售", "limit": 60}
        elif name == "get_order_draft":
            candidate = next(c for c in outputs["get_order_task"]["candidates"]
                             if c["customer"] == turn.customer)
            args = {"draft_id": candidate["draft_id"]}
        elif name == "revise_order_draft_quantity":
            draft = outputs.get("get_order_draft") or outputs["get_order_task"]["draft"]
            args = {"draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                    "item_number": turn.item, "quantity": turn.quantity,
                    "expected_sku": draft["items"][turn.item - 1]["sku"]}
        if name:
            yield ToolCall(f"call-{len(self.requests)}", name, json.dumps(args))
        elif turn.verify:
            draft = outputs.get("get_order_draft") or outputs["get_order_task"]["draft"]
            expected = cw.verification_expected(draft, turn)
            earlier = " ".join(m.get("content") or "" for m in messages)
            if expected["password"] and cw.PASSWORD not in earlier:
                expected["password"] = None
            yield TextDelta(json.dumps(expected, ensure_ascii=False))
        else:
            # Read the actual projected input, not the expected password value.
            earlier = " ".join(m.get("content") or "" for m in messages)
            password = cw.PASSWORD if cw.PASSWORD in earlier else "缺少早期口令"
            yield TextDelta(f"草稿核对完成 {password} {cw.SKUS[0]} {cw.SKUS[30]} 77")
        yield StreamEnd(None if self.unknown else Usage(100, 10, 110))


@pytest.mark.parametrize("workflow", cw.WORKFLOWS, ids=lambda w: w.id)
async def test_continuous_model_protocol_uses_real_mcp_and_checks_every_table(tmp_path, workflow):
    client = ScriptedClient()
    turns = await cw.run_workflow(workflow, "structured", client=client, directory=tmp_path)
    assert len(turns) == len(workflow.turns)
    assert all(t["result"]["passed"] for t in turns), [
        t["result"]["failed_checks"] for t in turns]
    assert all(cw.protocol_valid(r) for r in client.requests)
    assert all(len(t["result"]["state_evidence"]["all_tables"]) >= 7 for t in turns)
    assert max(sum(m["role"] == "user" for m in r) for r in client.requests) == len(turns)
    assert all(t["result"]["attempts"] == 1 for t in turns)


@pytest.mark.parametrize("fault", ["old_revision", "foreign_revision", "business", "attempt"])
def test_oracle_rejects_unrequested_changes_even_with_successful_text(tmp_path, fault):
    workflow = cw.WORKFLOWS[2]
    engine = cw.seed_workflow(tmp_path / "erp.db", workflow, "test")
    try:
        before = cw.snapshot_all(engine)
        after = deepcopy(before)
        result = CaseResult(case_id="t", category=CaseCategory.MULTI, passed=True,
                            tool_attempts=[])
        if fault == "old_revision":
            after["order_drafts"][0]["snapshot"] = "{}"
        elif fault == "foreign_revision":
            row = after["order_drafts"][0]
            after["order_drafts"].append({**row, "revision": row["revision"] + 1})
        elif fault == "business":
            after["stocks"][0]["quantity"] -= 1
        else:
            result.tool_attempts = ["submit_order_draft"]
        assert cw.check_turn(workflow.turns[0], result, before, after, "test")
    finally:
        engine.dispose()


@pytest.mark.parametrize("unknown", [False, True])
async def test_live_metering_partial_failure_and_immutable_output(tmp_path, monkeypatch, unknown):
    class SDK:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(cw, "AsyncOpenAI", SDK)
    monkeypatch.setattr(cw, "LLMClient", lambda *args, **kwargs: ScriptedClient(unknown=unknown))
    monkeypatch.setattr(cw, "WORKFLOWS", cw.WORKFLOWS[:1])
    monkeypatch.setattr(cw, "ROOT", tmp_path)
    config = ScriptedClient.config
    output = tmp_path / "batch"
    report = await cw.run_live(config, limit=Decimal("0.1"), currency="CNY", output=output)
    assert report["budget"]["unknown_usage"] is unknown
    assert report["journal_audit"]["unknown_requests"] == int(unknown)
    if unknown:
        assert report["journal_audit"]["requests"] == 1
        assert not report["complete"]
    else:
        assert report["results"][1]["passed"]
        assert all(Path(t["trace"]).is_file()
                   for r in report["results"] for t in r["turns"])
    with pytest.raises(ValueError, match="already exists"):
        await cw.run_live(config, limit=Decimal("0.1"), currency="CNY", output=output)

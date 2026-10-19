import json
from copy import deepcopy

import pytest
from agent_core.context import ContextPolicy
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import StreamEnd, TextDelta, ToolCall
from agent_core.runtime_config import LoopConfig
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from evals.context_experiment import CASES, fixture, project, protocol_valid, run_offline
from evals.context_live import grade
from evals.task_runtime import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.bridge import build_agent_tools_async
from mcp_erp.context_view import erp_context_view, summarize_task
from sqlalchemy.orm import Session


def test_frozen_experiment_preserves_limitations_and_input_integrity():
    report = run_offline()
    assert len(report["results"]) == len(CASES) * 3
    assert all(r["protocol_valid"] and r["source_unchanged"] for r in report["results"])
    rows = {(r["case_id"], r["strategy"]): r for r in report["results"]}
    assert rows["ctx-combined-48", "structured"]["retained"]
    assert not rows["ctx-combined-48", "current"]["retained"]
    assert not rows["ctx-unpersisted-constraint", "structured"]["retained"]
    assert not rows["ctx-protected-overflow", "structured"]["within_budget"]
    assert report["actual_usage"] is None and report["actual_cost"] is None
    assert report["cases_sha256"] == run_offline()["cases_sha256"]


def test_ambiguity_errors_and_user_data_do_not_gain_authority():
    for case in CASES:
        history, _ = fixture(case)
        original = deepcopy(history)
        view = project(history, "structured")
        assert history == original
        assert protocol_valid(view)
        assert [m for m in view if m["role"] == "system"] == [history[0]]
    assert summarize_task('{"error":{"message":"stop"}}') is None
    assert summarize_task("not json") is None


def test_live_scorer_rejects_coerced_numbers_and_added_claims():
    expected = {"status": "active", "quantity": 1}
    assert grade(json.dumps(expected), expected) == []
    assert grade('{"status":"active","quantity":true}', expected) == ["quantity"]
    assert grade('{"status":"active","quantity":1,"extra":"claim"}', expected)
    assert grade("done", expected) == ["invalid_json"]


@pytest.mark.parametrize("structured", [False, True])
async def test_projection_through_real_graph_mcp_and_isolated_draft(tmp_path, structured):
    path = tmp_path / "context.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for i in range(1, 61):
            sku = f"CTX{i:04}"
            session.add(ProductRow(sku=sku, name="长商品说明" * 60, category="实验",
                                   price=10, status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="实验仓"))
        session.commit()
    drafts = ErpDrafts(engine)
    drafts.prepare("context-smoke", "实验客户", [(f"CTX{i:04}", i) for i in range(1, 61)],
                   "分开发票，周五发货")
    before = snapshot_all(engine)
    requests = []

    class ReadingClient:
        async def stream_chat(self, messages, tools=None):
            requests.append(deepcopy(messages))
            if messages[-1]["role"] == "user":
                yield ToolCall("read-current", "get_order_task", "{}")
            elif messages[-1].get("tool_call_id") == "read-current":
                try:
                    payload = json.loads(messages[-1]["content"])
                    draft = payload["draft"]
                    item = draft["items"][30]
                except (ValueError, KeyError, IndexError):
                    yield TextDelta("无法读取完整草稿，未修改。")
                else:
                    yield ToolCall("revise", "revise_order_draft_quantity", json.dumps({
                        "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                        "item_number": 31, "quantity": 77, "expected_sku": item["sku"],
                    }))
            else:
                yield TextDelta("草稿操作结束；未提交订单。")
            yield StreamEnd(None)

    tools = await build_agent_tools_async(path, session_id="context-smoke")
    runtime = LangGraphRuntime(ReadingClient(), tools, checkpointer=InMemorySaver(),
                               config=LoopConfig(max_steps=4, context=ContextPolicy(),
                                                 context_view=erp_context_view()
                                                 if structured else None))
    messages = [{"role": "user", "content": "将当前版本第31行改为77，只保存草稿。"}]
    _ = [e async for e in runtime.run(messages, thread_id="context-smoke")]
    after = snapshot_all(engine)
    for name in before:
        if name != "order_drafts":
            assert after[name] == before[name], name
    current = drafts.task_state("context-smoke")["draft"]
    assert current["revision"] == (2 if structured else 1)
    assert current["items"][30]["quantity"] == (77 if structured else 31)
    assert current["note"] == "分开发票，周五发货"
    assert all(protocol_valid(request) for request in requests)
    if structured:
        snapshot = (await runtime.get_state("context-smoke")).values
        raw = next(m for m in snapshot["messages"] if m.get("tool_call_id") == "read-current")
        assert len(raw["content"]) > 4000
        assert json.loads(raw["content"])["draft"]["items"][30]["name"]
    engine.dispose()


@pytest.mark.parametrize("unknown", [False, True])
async def test_live_journal_settlement_and_unknown_usage_stop(tmp_path, monkeypatch, unknown):
    from decimal import Decimal

    from agent_core.llm import LLMConfig, Usage
    from evals import context_live

    class SDK:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Client:
        def __init__(self, config, **kwargs):
            self.config = config

        async def stream_chat(self, messages, tools=None):
            yield TextDelta("{}")  # An observed model failure must remain a failure.
            yield StreamEnd(None if unknown else Usage(100, 2, 102))

    monkeypatch.setattr(context_live, "AsyncOpenAI", SDK)
    monkeypatch.setattr(context_live, "LLMClient", Client)
    monkeypatch.setattr(context_live, "CASES", CASES[:1])
    output = tmp_path / "live"
    result = await context_live.run_live(
        LLMConfig("fake", base_url="https://flashcoding.ai/v1"),
        limit=Decimal("0.1"), currency="CNY", output=output,
    )
    assert len(result["results"]) == (1 if unknown else 3)
    assert result["complete"] is (not unknown)
    assert result["budget"]["unknown_usage"] is unknown
    assert result["journal_audit"]["unknown_requests"] == int(unknown)
    assert all(not row["passed"] for row in result["results"])
    with pytest.raises(ValueError, match="already exists"):
        await context_live.run_live(
            LLMConfig("fake", base_url="https://flashcoding.ai/v1"),
            limit=Decimal("0.1"), currency="CNY", output=output,
        )

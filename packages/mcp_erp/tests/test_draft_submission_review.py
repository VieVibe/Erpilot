import json

import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.events import ApprovalPending
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import StreamEnd, ToolCall
from erp_store.db import OrderDraftRow, ProductRow, StockRow, init_db, make_engine
from evals.task_runtime import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session


@pytest.mark.parametrize("change,code", [("price", "quote_changed"), ("stock", "stock_changed"),
                                      ("expired", "draft_expired"), ("budget", "budget_exceeded")])
async def test_live_blocker_ends_model_turn_before_approval_or_self_repair(tmp_path, change, code):
    path = tmp_path / "preflight.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        session.add(ProductRow(sku="A", name="商品甲", category="测试", price=100, status="在售"))
        session.add(StockRow(sku="A", quantity=20, warehouse="主仓"))
        session.commit()
    factory = await SessionErpTools.create(path, writes=True, gate=StreamApprovalGate())
    draft = factory.drafts.prepare("s", "客户", [("A", 2)],
                                   budget_limit=150 if change == "budget" else None)
    with Session(engine) as session:
        if change == "price":
            session.get(ProductRow, "A").price = 110
        elif change == "stock":
            session.get(StockRow, "A").quantity = 1
        elif change == "expired":
            session.get(OrderDraftRow, (draft["draft_id"], 1)).expires_at = "2000-01-01T00:00:00Z"
        session.commit()

    class Client:
        calls = 0

        async def stream_chat(self, messages, tools=None):
            self.calls += 1
            assert self.calls == 1, "blocked submission must not let a model repair the quote"
            yield ToolCall("submit", "submit_order_draft", json.dumps({
                "draft_id": draft["draft_id"], "revision": 1,
            }))
            yield StreamEnd()

    try:
        before = snapshot_all(engine)
        client = Client()
        runtime = LangGraphRuntime(client, factory.for_session("s"), checkpointer=InMemorySaver())
        events = [event async for event in runtime.stream(
            [{"role": "user", "content": "确认当前草稿，请提交等待审批，不修改草稿。"}],
            thread_id="blocked",
        )]
        assert not any(isinstance(event, ApprovalPending) for event in events)
        state = (await runtime.get_state("blocked")).values
        assert state["completed"] and code in state["tool_results"][0]["content"]
        assert client.calls == 1 and snapshot_all(engine) == before
        if change == "budget":
            # Bypassing the model/preflight cannot bypass the final transaction guard.
            from erp_store.mutations import MutationError

            with pytest.raises(MutationError) as exc:
                factory.drafts.submit("s", draft["draft_id"], 1, "budget-direct-submit")
            assert exc.value.code == "budget_exceeded"
            assert snapshot_all(engine) == before
    finally:
        engine.dispose()

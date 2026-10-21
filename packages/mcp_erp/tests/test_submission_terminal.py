import json

import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.events import ApprovalPending
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import StreamEnd, ToolCall
from erp_store.db import ProductRow, StockRow
from evals import context_workflow as cw
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session


@pytest.mark.parametrize("fault", ["revision", "price", "stock", "deny", "queued_write"])
async def test_blocked_submission_ends_before_model_or_queued_write_can_continue(tmp_path, fault):
    engine = cw.seed_workflow(tmp_path / "erp.db", cw.Workflow("terminal", ()), "s1")
    try:
        factory = await SessionErpTools.create(tmp_path / "erp.db", writes=True,
                                                gate=StreamApprovalGate())
        draft = factory.drafts.prepare("s1", "保留客户", [(cw.SKUS[0], 2)], "保留备注")

        class Client:
            requests = 0

            async def stream_chat(self, messages, tools=None):
                self.requests += 1
                if self.requests == 1:
                    yield ToolCall("read", "get_order_task", "{}")
                elif self.requests == 2:
                    for n in range(2 if fault == "queued_write" else 1):
                        yield ToolCall(f"submit-{n}", "submit_order_draft", json.dumps({
                            "draft_id": draft["draft_id"], "revision": 1,
                        }))
                else:
                    raise AssertionError("Model must not be invoked after a blocked submission")
                yield StreamEnd()

        client = Client()
        runtime = LangGraphRuntime(client, factory.for_session("s1"),
                                   checkpointer=InMemorySaver(),
                                   mutation_reconciler=factory.reconciler("s1"))
        pending = []
        async for event in runtime.stream([{"role": "user", "content": "提交当前草稿"}],
                                         thread_id="terminal"):
            if isinstance(event, ApprovalPending):
                pending.append(event)
        assert len(pending) == 1 and client.requests == 2
        if fault == "revision":
            factory.drafts.revise(
                "s1", draft["draft_id"], 1, "保留客户", [(cw.SKUS[0], 5)], "保留备注",
            )
        elif fault in {"price", "stock", "queued_write"}:
            with Session(engine) as session:
                if fault == "stock":
                    session.get(StockRow, cw.SKUS[0]).quantity = 999
                else:
                    session.get(ProductRow, cw.SKUS[0]).price = 20
                session.commit()
        before = cw.snapshot_all(engine)
        events = [event async for event in runtime.resume("terminal", {
            "pending_id": pending[0].pending_id, "approved": fault != "deny",
        })]
        assert not any(isinstance(e, ApprovalPending) for e in events)
        assert client.requests == 2 and cw.snapshot_all(engine) == before
        state = (await runtime.get_state("terminal")).values
        assert state["completed"] and "未" in state["final_answer"]
        assert all(m["role"] != "tool" or "tool_call_id" in m for m in state["messages"])
        assert cw.protocol_valid(state["messages"])
        if fault == "queued_write":
            assert state["tool_history"][-1]["invocation_status"] == "cancelled"
            assert "turn_stopped" in state["tool_history"][-1]["content"]
            assert len(state["tool_results"]) == 2
    finally:
        engine.dispose()

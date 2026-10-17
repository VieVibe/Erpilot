import json
import sqlite3
from datetime import timedelta

import httpx2
from agent_core.approval import StreamApprovalGate
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.db import OrderRow, make_engine
from erp_store.replenishment import BUSINESS_TZ
from erpilot_api.main import create_app
from evals.replenishment_cases import B6_AT, seed_replenishment_fixture
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.bridge import build_agent_tools_async
from mcp_erp.replenishment_tools import REPLENISHMENT_GUIDANCE
from sqlalchemy.orm import Session


def dump(path):
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())


async def test_sse_history_preserves_snapshot_without_requery_or_business_write(tmp_path):
    db = tmp_path / "erp.db"
    seed_replenishment_fixture(db)
    gate = StreamApprovalGate()
    tools = await build_agent_tools_async(db, writes=True, approval_gate=gate,
                                          session_id="s1", replenishment_at=B6_AT)

    def model(request):
        messages = json.loads(request.content)["messages"]
        assert REPLENISHMENT_GUIDANCE in messages[0]["content"]
        if messages[-1]["role"] == "tool":
            return sse_response([chunk(delta={"content": "建议补货37件，仅提供建议，未采购。"}),
                                 chunk(usage=USAGE)])
        return sse_response(tool_call_chunks(
            "b6", "suggest_replenishment", '{"skus":["B6-A"],"lead_time_days":7}'
        ))

    saver = InMemorySaver()
    options = dict(client_factory=lambda: make_client(model), tools=tools,
                   trace_dir=tmp_path / "traces", checkpointer=saver, approval_gate=gate)
    app = create_app(**options)
    before = dump(db)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/api/chat/stream",
                                     json={"session_id": "s1", "message": "给我补货建议"})
        assert response.status_code == 200 and "event: done" in response.text
        assert "event: approval_pending" not in response.text
        state = (await client.get("/api/sessions/s1/state")).json()
    display = state["tool_results"][0]["display"]
    assert display["kind"] == "replenishment_suggestion"
    assert display["suggestion"]["items"][0]["suggested_quantity"] == 37
    assert dump(db) == before

    # History is a saved result. Changed current facts must not silently rewrite it.
    engine = make_engine(db)
    with Session(engine) as session:
        for order in session.query(OrderRow).all():
            order.created_at = (B6_AT - timedelta(days=400)).astimezone(BUSINESS_TZ)
        session.commit()
    engine.dispose()
    changed = dump(db)
    restored = create_app(**options)
    recovered = await restored.state.service.session_state("s1")
    assert recovered["tool_results"][0]["display"] == display
    assert recovered["pending_approvals"] == [] and dump(db) == changed

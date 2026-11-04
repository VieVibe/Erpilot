"""A form save must be a real, persistent row reference for subsequent chat."""

import json

import httpx2
import pytest
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erpilot_api.checkpoint import thread_id_for_session
from erpilot_api.main import create_app
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session
from test_intent_semantic_api import chat, form, host


@pytest.mark.parametrize("source", [
    "第二行改成两件，其他信息保持不变",
    "把第二行 A1003（青瓷茶具·礼盒装）改成 2 件，其他不变",
])
async def test_form_save_survives_restart_and_authorizes_only_requested_line(tmp_path, source):
    engine = make_engine(tmp_path / "erp.db")
    init_db(engine)
    with Session(engine) as session:
        for sku, name, price in [("A1002", "青瓷茶具（家用装）", 245.8),
                                 ("A1003", "青瓷茶具（礼盒装）", 480.6)]:
            session.add(ProductRow(sku=sku, name=name, price=price, category="茶具", status="在售"))
            session.add(StockRow(sku=sku, quantity=75, warehouse="主仓"))
        session.commit()
    provider = await SessionErpTools.create(tmp_path / "erp.db", intent_mode="enforce")
    draft_id = None

    def handler(request):
        messages = json.loads(request.content)["messages"]
        if messages[-1]["role"] == "tool" and "total_amount" in messages[-1]["content"]:
            # Application's initial task read also contains a quote; distinguish the mutation.
            calls = {call["id"]: call["function"]["name"] for m in messages
                     for call in m.get("tool_calls", [])}
            if calls.get(messages[-1]["tool_call_id"]) == "revise_order_draft_quantity":
                return sse_response([chunk(delta={"content": "已保存"}), chunk(usage=USAGE)])
        return sse_response(tool_call_chunks("revise", "revise_order_draft_quantity", json.dumps({
            "draft_id": draft_id, "expected_revision": 1, "item_number": 2,
            "expected_sku": "A1003", "quantity": 2,
        })))

    def host():
        return create_app(client_factory=lambda: make_client(handler),
                          session_erp_tools=provider, checkpointer=InMemorySaver(),
                          trace_dir=tmp_path / "traces", run_store_path=tmp_path / "runs.db")

    app = host()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://test") as c:
        response = await c.post("/api/sessions/s/order-drafts", json={
            "customer": "王客户", "note": "分箱包装", "budget_limit": 1000.0,
            "items": [{"sku": "A1002", "quantity": 2}, {"sku": "A1003", "quantity": 1}],
        })
        assert response.status_code == 201
        draft_id = response.json()["draft_id"]
    # Restore both service and graph: form observations cannot live only in process memory.
    restarted = host()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(restarted), base_url="http://test",
    ) as c:
        response = await c.post("/api/chat/stream", json={"session_id": "s", "message": source})
        assert "event: done" in response.text and '"ok": false' not in response.text
        draft = (await c.get(f"/api/sessions/s/order-drafts/{draft_id}")).json()
        assert draft["revision"] == 2, response.text
        assert [row["quantity"] for row in draft["items"]] == [2, 2]
        assert draft["total_amount"] == 1452.8
        assert draft["budget_limit"] == 1000 and draft["budget_check"]["status"] == "over_budget"
        assert draft["customer"] == "王客户" and draft["note"] == "分箱包装"
    service = restarted.state.service
    history = await service._history("s")
    assert sum(m.get("tool_call_id", "").startswith("form-") for m in history) == 1
    assert not (await service._runtime("s").get_state(thread_id_for_session("s"))).next
    engine.dispose()


async def test_confirmed_semantic_form_records_actual_saved_fields_for_next_turn(tmp_path):
    app, provider, engine, _, _ = await host(tmp_path)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test",
        ) as c:
            await chat(c)
            preview = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
            response = await c.post("/api/sessions/s/pending-intent/confirm", json=form(preview))
            assert response.status_code == 201
            draft = response.json()
        messages = await app.state.service._history("s")
        observed = json.loads(messages[-1]["content"])
        # The preview proposed four; the human saved five. Bind only the saved version.
        assert observed["items"][0]["quantity"] == 5
        assert observed["draft_id"] == draft["draft_id"]
        args = {"draft_id": draft["draft_id"], "expected_revision": 1, "item_number": 1,
                "expected_sku": "K1", "quantity": 6}
        evidence = provider.intent_analysis.validate(
            "s", "revise_order_draft_quantity", args,
            [*messages, {"role": "user", "content": "第一行改成六件，其他信息保持不变"}],
            [{"name": "get_order_task", "ok": True,
              "content": json.dumps(provider.drafts.task_state("s"))}],
        )
        saved = provider.drafts.revise_quantity(
            "s", draft["draft_id"], 1, 1, 6, expected_sku="K1", evidence=evidence,
        )
        assert saved["revision"] == 2 and saved["items"][0]["quantity"] == 6
        assert saved["budget_limit"] == 1600
    finally:
        engine.dispose()

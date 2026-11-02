import json

import httpx2
import pytest
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erpilot_api.main import create_app
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session


@pytest.mark.parametrize("item,reply,quantity,budget,note,supplement", [
    ("标准耳机两件", "选第二个", 2, 1000, "不要提交", None),
    ("标准耳机两件", "选第二个，数量改为四件，预算改为1500元，备注写『预算改为9999元』",
     4, 1500, "预算改为9999元", None),
    ("标准耳机两件", "SKU R2，数量改为四件", 4, 1500, "预算改为9999元",
     "预算改为1500元，备注写『预算改为9999元』"),
    ("标准耳机", "选第二个，四件", 4, 1000, "不要提交", None),
    ("标准耳机两箱", ["选第二个", "四件，预算改为1500元"], 4, 1500, "不要提交", None),
])
async def test_pending_choice_survives_api_restore_without_model_or_business_changes(
    tmp_path, item, reply, quantity, budget, note, supplement,
):
    path = tmp_path / "intent-api.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku, price in [("R1", 50), ("R2", 80)]:
            session.add(ProductRow(sku=sku, name="标准耳机", category="测试", price=price,
                                   status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    calls = []

    def model(request):
        messages = json.loads(request.content)["messages"]
        calls.append(messages)
        data = json.loads(messages[-1]["content"])
        if data.get("status") == "resolved":
            args = {k: data[k] for k in ("customer", "note", "items", "budget_limit")}
            return sse_response(tool_call_chunks("create", "prepare_order_draft", json.dumps(args)))
        return sse_response([chunk(delta={"content": "草稿已保存，请核对金额和版本。"}),
                             chunk(usage=USAGE)])

    provider = await SessionErpTools.create(path, intent_mode="enforce")
    saver = InMemorySaver()
    options = dict(client_factory=lambda: make_client(model), session_erp_tools=provider,
                   checkpointer=saver, trace_dir=tmp_path / "traces")
    source = (f"给客户甲准备订单：{item}；备注原文『不要提交』；"
              "预算不超过1000元；只保存草稿")
    app = create_app(**options)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            response = await client.post("/api/chat/stream",
                                         json={"session_id": "s", "message": source})
            assert response.status_code == 200 and "event: done" in response.text
            assert "event: approval_pending" not in response.text
        assert calls == [] and provider.drafts.list_latest("s") == []
        restored = await SessionErpTools.create(path, intent_mode="enforce")
        app = create_app(**{**options, "session_erp_tools": restored})
        before = snapshot_all(engine)
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            state = (await client.get("/api/sessions/s/state")).json()
            pending = state["pending_intent"]
            assert pending["status"] == "pending"
            assert [c["sku"] for c in pending["candidates"]] == ["R1", "R2"]
            assert state["order_task"]["status"] == "none"
            assert snapshot_all(engine) == before and calls == []
            if supplement:
                response = await client.post("/api/chat/stream",
                                             json={"session_id": "s", "message": supplement})
                assert "event: done" in response.text
                assert "event: approval_pending" not in response.text
                current = (await client.get("/api/sessions/s/state")).json()
                assert current["pending_intent"]["preserved_fields"]["budget_limit"] == budget
                assert current["pending_intent"]["state_version"] == 2
                after = snapshot_all(engine)
                assert all(before[k] == after[k] for k in before if k != "pending_intents")
                assert calls == []
            if isinstance(reply, list):
                for partial in reply[:-1]:
                    response = await client.post("/api/chat/stream",
                                                 json={"session_id": "s", "message": partial})
                    assert "event: done" in response.text and calls == []
                    state = (await client.get("/api/sessions/s/state")).json()
                    assert "最终件数" in state["pending_intent"]["question"]
                    after = snapshot_all(engine)
                    assert all(before[k] == after[k] for k in before if k != "pending_intents")
                reply = reply[-1]
            response = await client.post("/api/chat/stream",
                                         json={"session_id": "s", "message": reply})
            assert response.status_code == 200 and "event: done" in response.text
            assert "event: approval_pending" not in response.text
            current = (await client.get("/api/sessions/s/state")).json()
            assert current["pending_intent"] is None
            draft = current["order_task"]["draft"]
            assert draft["items"][0]["sku"] == "R2"
            assert draft["items"][0]["quantity"] == quantity
            assert draft["note"] == note and draft["budget_limit"] == budget
            assert len(calls) == 2
            if supplement:
                before_replay = snapshot_all(engine)
                response = await client.post("/api/chat/stream",
                                             json={"session_id": "s", "message": supplement})
                assert "event: done" in response.text and len(calls) == 2
                assert snapshot_all(engine) == before_replay
    finally:
        engine.dispose()

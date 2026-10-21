import httpx2
import pytest
from erp_store.db import OrderDraftRow, ProductRow, StockRow, init_db, make_engine
from erpilot_api.main import create_app
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def erp_path(tmp_path):
    path = tmp_path / "erp.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        session.add(ProductRow(sku="A", name="商品", category="测试", price=100, status="在售"))
        session.add(StockRow(sku="A", quantity=1000, warehouse="主仓"))
        session.commit()
    engine.dispose()
    return path


async def test_form_history_restores_sources_pages_and_isolates_sessions(tmp_path, erp_path):
    provider = await SessionErpTools.create(erp_path)
    app = create_app(session_erp_tools=provider, trace_dir=tmp_path, checkpointer=InMemorySaver())
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        base = "/api/sessions/s1/order-drafts"
        body = {"customer": "完整表单客户", "note": "原备注",
                "items": [{"sku": "A", "quantity": 2}]}
        created = (await client.post(base, json=body)).json()
        route = f"{base}/{created['draft_id']}"
        assert created["task_constraints"]["fields"]["customer"]["origin"]["kind"] == "manual_form"
        for revision in range(1, 4):
            response = await client.patch(route, json={
                **body, "note": None if revision == 3 else "原备注",
                "expected_revision": revision, "items": [{"sku": "A", "quantity": revision + 2}],
            })
            assert response.status_code == 200
        fresh = await SessionErpTools.create(erp_path)
        restored = create_app(session_erp_tools=fresh, trace_dir=tmp_path / "restored",
                              checkpointer=InMemorySaver())
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(restored), base_url="http://test",
    ) as client:
        page = (await client.get(route + "/history?limit=2")).json()
        assert [item["revision"] for item in page["items"]] == [4, 3]
        assert page["next_before_revision"] == 3
        older = (await client.get(route + "/history?limit=2&before_revision=3")).json()
        assert [item["revision"] for item in older["items"]] == [2, 1]
        assert not older["has_more"] and older["next_before_revision"] is None
        assert page["items"][0]["task_constraints"]["fields"]["note"]["value"] is None
        foreign = await client.get(route.replace("/s1/", "/s2/") + "/history")
        missing = await client.get(base + "/missing/history")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
        for query in ("limit=0", "limit=21", "before_revision=0", "before_revision=wrong"):
            invalid = await client.get(route + "/history?" + query)
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "invalid_argument"
    engine = make_engine(erp_path)
    try:
        with Session(engine) as session:
            assert session.get(StockRow, "A").quantity == 1000
            assert len(list(session.scalars(select(OrderDraftRow)))) == 4
    finally:
        engine.dispose()


async def test_disabled_history_has_structured_error(tmp_path):
    app = create_app(tools=[], trace_dir=tmp_path, checkpointer=InMemorySaver())
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        response = await client.get("/api/sessions/s1/order-drafts/any/history")
    assert response.status_code == 503 and response.json()["error"]["code"] == "drafts_disabled"

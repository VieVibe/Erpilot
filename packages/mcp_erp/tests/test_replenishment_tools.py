import sqlite3

import pytest
from agent_core.approval import AutoDenyGate
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from fastmcp import Client
from mcp_erp.bridge import WRITE_TOOL_RISK, build_agent_tools_async
from mcp_erp.server import create_server
from sqlalchemy.orm import Session


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "erp.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        session.add(ProductRow(sku="A", name="商品甲", category="测试", price=10, status="在售"))
        session.add(StockRow(sku="A", quantity=3, warehouse="主仓"))
        session.commit()
    engine.dispose()
    return path


def dump(path):
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())


@pytest.mark.parametrize("writes", [False, True])
@pytest.mark.parametrize("scope", [None, "s1"])
async def test_tool_is_readonly_across_surfaces(db, writes, scope):
    before = dump(db)
    async with Client(create_server(db, include_writes=writes, session_id=scope)) as client:
        tools = {t.name: t.input_schema for t in await client.list_tools()}
        assert "suggest_replenishment" in tools
        assert "session_id" not in tools["suggest_replenishment"]["properties"]
        payload = (await client.call_tool("suggest_replenishment", {"skus": ["A"]})).data
    assert payload["items"][0]["decision"] == "no_sales"
    assert dump(db) == before
    tools = await build_agent_tools_async(
        db, writes=writes, approval_gate=AutoDenyGate(), session_id=scope
    )
    tool = next(t for t in tools if t.name == "suggest_replenishment")
    assert tool.risk is None and tool.name not in WRITE_TOOL_RISK
    payload = await tool.handler(tool.params_model(skus=["A"], lead_time_days=0))
    assert payload["lead_time_days"] == 0 and payload["total"] == 1
    assert dump(db) == before


async def test_errors_survive_real_mcp_and_bridge(db):
    async with Client(create_server(db)) as client:
        for args, code in [({"days": 0}, "invalid_argument"),
                           ({"skus": []}, "invalid_argument"),
                           ({"lead_time_days": -1}, "invalid_argument"),
                           ({"skus": ["MISSING"]}, "not_found")]:
            result = (await client.call_tool("suggest_replenishment", args)).data
            assert result["error"]["code"] == code

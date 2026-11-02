import os
import runpy
import subprocess
import sys
from contextlib import aclosing
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from agent_core.events import ApprovalPending
from erp_store.db import make_engine
from erp_store.repository import ErpRepository


@pytest.fixture
def demo():
    script = Path(__file__).resolve().parents[3] / "scripts" / "demo.py"
    return runpy.run_path(str(script))


def test_importing_factory_does_not_open_default_erp_or_run_store(tmp_path):
    env = {**os.environ, "ERPILOT_TOOLS": "mcp", "ERPILOT_WRITES": "true"}
    result = subprocess.run(
        [sys.executable, "-c", "from erpilot_api.main import create_app; print('factory-only')"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20, check=True,
    )
    assert result.stdout.strip() == "factory-only"
    assert list(tmp_path.iterdir()) == []


async def test_offline_demo_verifies_all_business_flows_without_external_fallback(
    tmp_path, demo, monkeypatch,
):
    def forbidden(*args, **kwargs):
        pytest.fail("offline demo attempted an external model, embedding or trace sink")

    monkeypatch.setattr("erpilot_api.service._sinks_from_env", forbidden)
    monkeypatch.setattr("agent_core.llm.LLMConfig.from_env", forbidden)
    monkeypatch.setattr("mcp_erp.server._default_policy_resolver", forbidden)
    monkeypatch.setattr("httpx2.AsyncHTTPTransport.handle_async_request", forbidden)
    results = await demo["run_demo"](tmp_path)
    assert {r["scenario"] for r in results} == set(demo["SCENARIOS"])
    assert len(results) == 11
    assert all(r["verified"] for r in results)
    assert all(Path(path).is_file() for r in results for path in r["traces"])
    for result in results:
        assert result["source"] == "scripted-demo"
        if result["scenario"] in {
            "quote", "policy", "policy_miss", "cancellation", "replenishment",
        }:
            assert result["changed_tables"] == [] and result["approvals"] == 0
    orders = {r["scenario"]: r for r in results if r["scenario"].startswith("order")}
    assert orders["order"]["after"] == 990
    assert orders["order_denied"]["after"] == 1000
    assert (tmp_path / "summary.json").is_file()


@pytest_asyncio.fixture
async def browser_demo(tmp_path, demo):
    db = tmp_path / "erp.db"
    demo["seed_demo_database"](db)
    app = await demo["create_demo_app"](db, tmp_path / "traces")
    engine = make_engine(db)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        yield client, ErpRepository(engine), demo, app
    if app.state.service._client:
        await app.state.service._client._client.close()
    engine.dispose()


async def test_demo_unknown_input_and_new_session_cannot_mutate_or_reuse_draft(browser_demo):
    client, repo, demo, _ = browser_demo
    runtime = (await client.get("/api/runtime")).json()
    assert runtime["scripted_demo"] and runtime["drafts_enabled"]
    assert runtime["business_approvals_enabled"]
    response = await client.post("/api/chat/stream", json={"session_id": "a", "message": "你好"})
    assert "tool_started" not in response.text and "approval_pending" not in response.text
    assert "固定演示指令" in response.text
    await client.post(
        "/api/chat/stream", json={"session_id": "a", "message": demo["PROMPTS"]["draft"]},
    )
    response = await client.post(
        "/api/chat/stream", json={"session_id": "b", "message": demo["PROMPTS"]["submit"]},
    )
    assert "approval_pending" not in response.text and "没有唯一" in response.text
    assert (await client.get("/api/sessions/b/order-drafts")).json() == {"items": []}
    assert repo.count_orders(customer=demo["CUSTOMER"]) == 0
    assert repo.get_stock("A1001").quantity == 1000


@pytest.mark.parametrize("approved", [True, False])
async def test_demo_pending_reload_uses_exact_revision_and_approval_once(browser_demo, approved):
    client, repo, demo, app = browser_demo
    for key in ("draft", "revise", "submit"):
        # 关闭提交轮的 SSE 对应页面断线；再经 HTTP snapshot + resume 恢复审批。
        async with aclosing(app.state.service.stream_run("a", demo["PROMPTS"][key])) as stream:
            async for event in stream:
                if isinstance(event, ApprovalPending):
                    break
    state = (await client.get("/api/sessions/a/state")).json()
    pending = state["pending_approvals"][0]
    assert state["status"] == "waiting_approval"
    assert pending["draft"]["revision"] == 2 and pending["draft"]["total_amount"] == 3355
    assert repo.get_stock("B2001").quantity == 300
    body = {"session_id": "a", "pending_id": pending["pending_id"], "approved": approved}
    response = await client.post("/api/chat/approve/stream", json=body)
    assert response.status_code == 200 and "event: done" in response.text
    assert (await client.post("/api/chat/approve/stream", json=body)).status_code == 409
    assert repo.count_orders(customer=demo["CUSTOMER"]) == int(approved)
    assert repo.get_stock("B2001").quantity == (250 if approved else 300)

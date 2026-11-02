"""Human-reviewed revision -> durable HITL -> real MCP, with no model client."""

import asyncio
from contextlib import suppress

import httpx2
import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.events import ApprovalPending
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import ToolCall
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.repository import ErpRepository
from erpilot_api.main import create_app
from erpilot_api.service import SessionError
from evals.task_runtime import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "erp.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku, price in (("A", 100), ("B", 20)):
            session.add(ProductRow(sku=sku, name=sku, category="测试", price=price, status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    yield path, engine
    engine.dispose()


def forbidden_model():
    raise AssertionError("Confirmed action must never initialize or request a model")


async def host(tmp_path, database, saver=None, *, writes=True):
    class ReadyGate(StreamApprovalGate):
        def __init__(self):
            super().__init__()
            self.ready = asyncio.Event()

        def register(self, pending_id):
            future = super().register(pending_id)
            self.ready.set()
            return future

    provider = await SessionErpTools.create(database[0], writes=writes,
                                            gate=ReadyGate() if writes else None)
    app = create_app(client_factory=forbidden_model, session_erp_tools=provider,
                     trace_dir=tmp_path, checkpointer=saver or InMemorySaver())
    return app, provider


def prepare(provider, session_id="s1"):
    return provider.drafts.prepare(session_id, "完整客户甲", [("A", 2), ("B", 3)], "保留原备注",
                                   evidence={"contract": "draft-form-1"})


async def suspend(app, draft):
    frozen, release = await app.state.service.reserve_draft_submission(
        "s1", draft["draft_id"], draft["revision"],
    )
    stream = app.state.service.stream_confirmed_draft("s1", frozen)
    try:
        async for event in stream:
            if isinstance(event, ApprovalPending):
                return event
        pytest.fail("expected approval")
    finally:
        await stream.aclose()
        release()


async def wait_pending(service):
    await asyncio.wait_for(service._approval_gate.ready.wait(), 5)
    return (await service.session_state("s1"))["pending_approvals"][0]


def quote_content(draft):
    return {**draft, "submission_check": {
        key: value for key, value in draft["submission_check"].items() if key != "checked_at"
    }}


@pytest.mark.parametrize("change,code", [("price", "quote_changed"), ("stock", "stock_changed")])
async def test_current_pending_issues_block_http_confirmation_before_approval(
    tmp_path, database, change, code,
):
    app, provider = await host(tmp_path, database)
    first = prepare(provider)
    with Session(database[1]) as session:
        if change == "price":
            session.get(ProductRow, "A").price = 200
        else:
            session.get(StockRow, "A").quantity = 1
        session.commit()
    before = snapshot_all(database[1])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        response = await client.get(f"/api/sessions/s1/order-drafts/{first['draft_id']}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["pending_issues"][0]["code"] == code
        assert not payload["submission_check"]["can_submit"]
        assert payload["items"] == first["items"]
        blocked = await client.post(
            f"/api/sessions/s1/order-drafts/{first['draft_id']}/confirm/stream",
            json={"expected_revision": 1},
        )
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == code
    assert snapshot_all(database[1]) == before
    with pytest.raises(SessionError) as exc:
        await app.state.service.session_state("s1")
    assert exc.value.code == "session_not_found"


@pytest.mark.parametrize("approved", [False, True])
async def test_http_confirmation_is_only_an_approval_request_and_uses_no_model(
    tmp_path, database, approved,
):
    app, provider = await host(tmp_path, database)
    draft = prepare(provider)
    before = snapshot_all(database[1])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        task = asyncio.create_task(client.post(
            f"/api/sessions/s1/order-drafts/{draft['draft_id']}/confirm/stream",
            json={"expected_revision": 1},
        ))
        try:
            pending = await wait_pending(app.state.service)
            assert pending["arguments"]["draft_id"] == draft["draft_id"]
            assert pending["arguments"]["revision"] == 1
            assert quote_content(pending["draft"]) == quote_content(draft)
            assert snapshot_all(database[1]) == before
            duplicate = await client.post(
                f"/api/sessions/s1/order-drafts/{draft['draft_id']}/confirm/stream",
                json={"expected_revision": 1},
            )
            assert duplicate.status_code == 409
            response = await client.post("/api/chat/approve", json={
                "pending_id": pending["pending_id"], "approved": approved,
            })
            assert response.json() == {"ok": True}
            result = await asyncio.wait_for(task, 5)
            assert result.status_code == 200 and "event: done" in result.text
            assert '"execution_mode": "confirmed_action"' in result.text
            assert '"usage": null' in result.text
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    repo = ErpRepository(database[1])
    assert repo.count_orders(customer="完整客户甲") == int(approved)
    assert repo.get_stock("A").quantity == 1000 - (2 if approved else 0)
    state = await app.state.service.session_state("s1")
    assert state["status"] == "completed" and state["messages"][-1]["role"] == "assistant"
    assert ("已创建" if approved else "已拒绝") in state["messages"][-1]["content"]


@pytest.mark.parametrize("fault", ["foreign", "stale", "expired", "readonly", "bool", "extra"])
async def test_unreviewed_or_unavailable_revisions_fail_before_creating_a_run(
    tmp_path, database, fault,
):
    app, provider = await host(tmp_path, database, writes=fault != "readonly")
    draft = prepare(provider, "foreign" if fault == "foreign" else "s1")
    if fault == "stale":
        provider.drafts.revise("s1", draft["draft_id"], 1, "完整客户甲", [("A", 5), ("B", 3)],
                               "保留原备注", evidence={"contract": "draft-form-1"})
    if fault == "expired":
        from erp_store.db import OrderDraftRow
        with Session(database[1]) as session:
            row = session.get(OrderDraftRow, (draft["draft_id"], 1))
            row.expires_at = "2000-01-01T00:00:00+00:00"
            session.commit()
    before = snapshot_all(database[1])
    body = {"expected_revision": True if fault == "bool" else 1}
    if fault == "extra":
        body["approved"] = True
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        result = await client.post(
            f"/api/sessions/s1/order-drafts/{draft['draft_id']}/confirm/stream", json=body,
        )
    assert result.status_code == {"foreign": 404, "stale": 409, "expired": 409,
                                  "readonly": 403, "bool": 422, "extra": 422}[fault]
    assert snapshot_all(database[1]) == before and not app.state.service.lock("s1").locked()


async def test_review_new_revision_then_price_drift_refresh_and_reconfirm_exact_version(
    tmp_path, database,
):
    app, provider = await host(tmp_path, database)
    draft = prepare(provider)
    approval = await suspend(app, draft)
    revised = provider.drafts.revise("s1", draft["draft_id"], 1, "完整客户甲", [("A", 5), ("B", 3)],
                                     "保留原备注", evidence={"contract": "draft-form-1"})
    events = [e async for e in app.state.service.stream_resume("s1", approval.pending_id, True)]
    assert events[-1].completed
    state = await app.state.service.session_state("s1")
    assert "draft_changed" in state["tool_results"][0]["content"]
    approval2 = await suspend(app, revised)
    assert approval2.pending_id != approval.pending_id
    with Session(database[1]) as session:
        session.get(ProductRow, "A").price = 200
        session.commit()
    events = [e async for e in app.state.service.stream_resume("s1", approval2.pending_id, True)]
    assert events[-1].completed
    state = await app.state.service.session_state("s1")
    assert "quote_changed" in state["tool_results"][0]["content"]
    repo = ErpRepository(database[1])
    assert repo.count_orders(customer="完整客户甲") == 0 and repo.get_stock("A").quantity == 1000
    refreshed = provider.drafts.revise(
        "s1", draft["draft_id"], 2, "完整客户甲", [("A", 5), ("B", 3)],
        "保留原备注", evidence={"contract": "draft-form-1"},
    )
    approval3 = await suspend(app, refreshed)
    assert approval3.arguments["revision"] == 3
    events = [e async for e in app.state.service.stream_resume("s1", approval3.pending_id, True)]
    assert events[-1].completed
    state = await app.state.service.session_state("s1")
    assert "1060.00" in state["messages"][-1]["content"]
    assert repo.count_orders(customer="完整客户甲") == 1 and repo.get_stock("A").quantity == 995
    assert provider.drafts.get("s1", draft["draft_id"], 1)["total_amount"] == 260
    assert provider.drafts.history("s1", draft["draft_id"])["latest_revision"] == 3
    assert refreshed["task_constraints"] == draft["task_constraints"]
    assert refreshed["item_constraint"]["value"] == [{"sku": "A", "quantity": 5},
                                                      {"sku": "B", "quantity": 3}]


@pytest.mark.parametrize("committed", [False, True])
async def test_restored_pending_confirmation_keeps_identity_and_recovers_one_order(
    tmp_path, database, committed,
):
    saver = InMemorySaver()
    app, provider = await host(tmp_path, database, saver)
    draft = prepare(provider)
    approval = await suspend(app, draft)
    if committed:
        provider.drafts.submit("s1", draft["draft_id"], 1, approval.arguments["client_token"])
    restored, _ = await host(tmp_path, database, saver)
    state = await restored.state.service.session_state("s1")
    assert state["pending_approvals"][0]["pending_id"] == approval.pending_id
    events = [e async for e in restored.state.service.stream_resume(
        "s1", approval.pending_id, True,
    )]
    assert events[-1].completed
    state = await restored.state.service.session_state("s1")
    assert "已创建" in state["messages"][-1]["content"]
    assert ErpRepository(database[1]).count_orders(customer="完整客户甲") == 1
    assert ErpRepository(database[1]).get_stock("A").quantity == 998
    with pytest.raises(SessionError, match="审批已失效"):
        _ = [e async for e in restored.state.service.stream_resume("s1", approval.pending_id, True)]
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(restored), base_url="http://test",
    ) as client:
        duplicate = await client.post(
            f"/api/sessions/s1/order-drafts/{draft['draft_id']}/confirm/stream",
            json={"expected_revision": 1},
        )
        assert duplicate.status_code == 409


async def test_direct_runtime_rejects_tools_without_explicit_approved_action_support(
    tmp_path, database,
):
    _, provider = await host(tmp_path, database)
    runtime = LangGraphRuntime(None, provider.for_session("s1"), checkpointer=InMemorySaver())
    with pytest.raises(ValueError, match="direct action"):
        _ = [e async for e in runtime.stream_call([], ToolCall("bad", "get_order_task", "{}"),
                                                 thread_id="rejected")]


async def test_budget_is_preserved_by_http_edits_and_blocks_confirmation_until_explicit_change(
    tmp_path, database,
):
    app, provider = await host(tmp_path, database)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        body = {"customer": "预算客户", "items": [{"sku": "A", "quantity": 2},
                {"sku": "B", "quantity": 3}], "note": "保留", "budget_limit": 300}
        first = (await client.post("/api/sessions/s1/order-drafts", json=body)).json()
        path = f"/api/sessions/s1/order-drafts/{first['draft_id']}"
        edited = {**body, "expected_revision": 1,
                  "items": [{"sku": "A", "quantity": 4}, {"sku": "B", "quantity": 3}]}
        edited.pop("budget_limit")
        second = (await client.patch(path, json=edited)).json()
        assert second["budget_limit"] == 300 and second["budget_check"]["over_amount"] == 160
        before = snapshot_all(database[1])
        rejected = await client.post(path + "/confirm/stream", json={"expected_revision": 2})
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "budget_exceeded"
        assert snapshot_all(database[1]) == before and not app.state.service.lock("s1").locked()
        third = (await client.patch(path, json={**edited, "expected_revision": 2,
                                               "budget_limit": 500})).json()
        assert third["revision"] == 3 and not third["pending_issues"]
        assert (await client.get(path + "?revision=1")).json()["budget_limit"] == 300
        state = (await client.get("/api/sessions/s1/order-task")).json()
        assert state["draft"]["budget_limit"] == 500
        history = (await client.get(path + "/history")).json()
        assert history["items"][0]["budget_constraint"]["origin"]["revision"] == 3
    approval = await suspend(app, third)
    events = [e async for e in app.state.service.stream_resume("s1", approval.pending_id, True)]
    assert events[-1].completed
    assert ErpRepository(database[1]).count_orders(customer="预算客户") == 1
    assert ErpRepository(database[1]).get_stock("A").quantity == 996


@pytest.mark.parametrize("budget", [True, "300", 0, -1, 1.001])
async def test_invalid_http_budget_has_no_writes(tmp_path, database, budget):
    app, _ = await host(tmp_path, database)
    before = snapshot_all(database[1])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test",
    ) as client:
        response = await client.post("/api/sessions/s1/order-drafts", json={
            "customer": "客户", "items": [{"sku": "A", "quantity": 1}], "budget_limit": budget,
        })
    assert response.status_code == 422 and snapshot_all(database[1]) == before


async def test_budget_change_invalidates_restored_approval_and_requires_new_confirmation(
    tmp_path, database,
):
    saver = InMemorySaver()
    app, provider = await host(tmp_path, database, saver)
    first = provider.drafts.prepare("s1", "客户", [("A", 2)], budget_limit=300,
                                     evidence={"contract": "draft-form-1"})
    pending = await suspend(app, first)
    second = provider.drafts.revise("s1", first["draft_id"], 1, "客户", [("A", 2)],
                                    budget_limit=100, evidence={"contract": "draft-form-1"})
    restored, _ = await host(tmp_path, database, saver)
    events = [e async for e in restored.state.service.stream_resume("s1", pending.pending_id, True)]
    assert events[-1].completed
    state = await restored.state.service.session_state("s1")
    assert "draft_changed" in state["tool_results"][0]["content"]
    assert second["pending_issues"] and ErpRepository(database[1]).count_orders() == 0

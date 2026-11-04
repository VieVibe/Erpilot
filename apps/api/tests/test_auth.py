"""Actual API/MCP authorization: cookies, CSRF, identity and write boundaries."""

import json
import runpy
from contextlib import aclosing
from pathlib import Path

import httpx2
import pytest
from agent_core.events import ApprovalPending
from erp_store.db import make_engine
from erp_store.repository import ErpRepository
from erpilot_api.auth_store import COOKIE, AuthError, AuthStore
from erpilot_api.main import create_app
from langgraph.checkpoint.memory import InMemorySaver

PASSWORD = "a-strong-test-password-2026"


@pytest.fixture
async def secured(tmp_path):
    demo = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/demo.py"))
    db = tmp_path / "erp.db"
    demo["seed_demo_database"](db)
    store = AuthStore(tmp_path / "runs.db")
    users = {
        name: store.add_user(name, PASSWORD, role)
        for name, role in (("alice", "approver"), ("bob", "approver"), ("reader", "operator"))
    }
    from agent_core.approval import StreamApprovalGate
    from mcp_erp.session_tools import SessionErpTools

    provider = await SessionErpTools.create(
        db,
        writes=True,
        gate=StreamApprovalGate(),
        policy_resolver=demo["demo_policy_resolver"](),
    )
    app = create_app(
        client_factory=demo["demo_client"],
        model=demo["MODEL"],
        trace_dir=tmp_path / "traces",
        session_erp_tools=provider,
        checkpointer=InMemorySaver(),
        scripted_demo=True,
        run_store_path=store.path,
        auth_store=store,
    )
    engine = make_engine(db)
    clients = []

    async def client(name=None):
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://test")
        clients.append(http)
        if name:
            response = await http.post(
                "/api/auth/login", json={"username": name, "password": PASSWORD}
            )
            assert response.status_code == 200, response.text
            http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return http

    yield app, store, users, client, ErpRepository(engine), demo
    for http in clients:
        await http.aclose()
    if app.state.service._client:
        await app.state.service._client._client.close()
    engine.dispose()


async def test_login_cookie_csrf_logout_and_no_plaintext_storage(secured):
    app, store, _, client, _, _ = secured
    anonymous = await client()
    assert (await anonymous.get("/healthz")).status_code == 200
    assert (await anonymous.get("/api/auth/status")).json() == {"enabled": True}
    assert (await anonymous.get("/api/sessions")).status_code == 401
    assert (await anonymous.post("/api/chat/stream", json={"message": "报价"})).status_code == 401
    wrong = await anonymous.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert wrong.status_code == 401 and "wrong" not in wrong.text
    logged = await anonymous.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    cookie = logged.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = anonymous.cookies[COOKIE]
    assert (
        token.encode() not in store.path.read_bytes()
        and PASSWORD.encode() not in store.path.read_bytes()
    )
    assert (await anonymous.post("/api/sessions")).status_code == 403
    csrf = logged.json()["csrf_token"]
    anonymous.headers["x-csrf-token"] = csrf
    assert (await anonymous.post("/api/sessions")).status_code == 201
    # Non-ASCII malformed headers must fail closed rather than raising TypeError.
    assert (
        await anonymous.post("/api/sessions", headers={b"x-csrf-token": b"\xff"})
    ).status_code == 403
    assert (
        await anonymous.post("/api/sessions", headers={"origin": "https://attacker.invalid"})
    ).status_code == 403
    assert (await anonymous.post("/api/auth/logout")).status_code == 200
    assert (await anonymous.get("/api/auth/me")).status_code == 401
    assert app.state.auth_store is store


async def test_resource_ownership_history_events_and_legacy_adoption(secured):
    app, store, users, client, _, _ = secured
    alice, bob = await client("alice"), await client("bob")
    sid = (await alice.post("/api/sessions")).json()["session_id"]
    response = await alice.post("/api/chat/stream", json={"session_id": sid, "message": "报价"})
    assert "event: done" in response.text
    created = await alice.post(
        f"/api/sessions/{sid}/order-drafts",
        json={
            "customer": "private customer",
            "items": [{"sku": "A1001", "quantity": 2}],
        },
    )
    assert created.status_code == 201
    draft = created.json()
    with store.connect() as db:
        run_id = db.execute("SELECT run_id FROM run WHERE session_id=?", (sid,)).fetchone()[0]
    for path in (
        f"/api/sessions/{sid}/state",
        f"/api/sessions/{sid}/order-task",
        f"/api/sessions/{sid}/order-drafts/{draft['draft_id']}",
        f"/api/sessions/{sid}/order-drafts/{draft['draft_id']}/history",
        f"/api/runs/{run_id}/events",
    ):
        denied = await bob.get(path)
        assert denied.status_code == 404 and "private customer" not in denied.text
    assert (
        await bob.post("/api/chat/stream", json={"session_id": sid, "message": "提交当前草稿"})
    ).status_code == 404
    assert [r["session_id"] for r in (await alice.get("/api/sessions")).json()["items"]] == [sid]
    assert (await bob.get("/api/sessions")).json()["items"] == []
    # Existing pre-auth business history remains unowned and cannot be claimed by guessing its ID.
    legacy = "legacy-before-auth"
    app.state.run_store.create_run(legacy, "old", [], None)
    assert (await alice.get(f"/api/sessions/{legacy}/state")).status_code == 404
    new = (
        await bob.post("/api/sessions", json={"session_id": sid, "user_id": users["alice"].user_id})
    ).json()
    assert new["session_id"] != sid
    store.require_owner(users["bob"], new["session_id"])


async def test_invalid_login_envelopes_and_secure_cookie_configuration(secured, monkeypatch):
    _, _, _, client, _, _ = secured
    anonymous = await client()
    for kwargs, status in (
        ({}, 422),
        ({"json": []}, 422),
        ({"content": b"{broken", "headers": {"content-type": "application/json"}}, 422),
        ({"content": b"<xml/>", "headers": {"content-type": "text/xml"}}, 415),
        ({"content": b"x" * (2 * 1024 * 1024 + 1)}, 413),
    ):
        response = await anonymous.post("/api/auth/login", **kwargs)
        assert response.status_code == status
        assert "error" in response.json()
    monkeypatch.setenv("ERPILOT_COOKIE_SECURE", "1")
    logged = await anonymous.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert logged.status_code == 200 and "; Secure" in logged.headers["set-cookie"]


async def test_pending_approval_cannot_be_stolen_and_decision_is_audited(secured):
    app, _, _, client, repo, demo = secured
    alice, bob = await client("alice"), await client("bob")
    sid = (await alice.post("/api/sessions")).json()["session_id"]
    other = (await bob.post("/api/sessions")).json()["session_id"]
    await alice.post(
        "/api/chat/stream", json={"session_id": sid, "message": demo["PROMPTS"]["draft"]}
    )
    async with aclosing(app.state.service.stream_run(sid, demo["PROMPTS"]["submit"])) as events:
        async for event in events:
            if isinstance(event, ApprovalPending):
                pending = event.pending_id
                break
    for target in (sid, other):
        denied = await bob.post(
            "/api/chat/approve",
            json={
                "session_id": target,
                "pending_id": pending,
                "approved": True,
            },
        )
        assert denied.status_code == 404
    assert repo.count_orders(customer=demo["CUSTOMER"]) == 0
    approved = await alice.post(
        "/api/chat/approve/stream",
        json={
            "session_id": sid,
            "pending_id": pending,
            "approved": True,
        },
    )
    assert approved.status_code == 200 and "event: done" in approved.text
    assert repo.count_orders(customer=demo["CUSTOMER"]) == 1
    repeat = await alice.post(
        "/api/chat/approve/stream",
        json={
            "session_id": sid,
            "pending_id": pending,
            "approved": True,
        },
    )
    assert repeat.status_code == 409 and repo.count_orders(customer=demo["CUSTOMER"]) == 1
    audits = (await alice.get("/api/auth/audit")).json()["items"]
    assert any(
        row["action"] == "approval_request"
        and row["detail"].get("pending_id") == pending
        and row["detail"].get("approved") is True
        for row in audits
    )
    assert PASSWORD not in json.dumps(audits) and "csrf_token" not in json.dumps(audits)


async def test_operator_cannot_execute_model_or_direct_write(secured):
    _, _, _, client, repo, demo = secured
    reader = await client("reader")
    sid = (await reader.post("/api/sessions")).json()["session_id"]
    assert (await reader.get("/api/runtime")).json()["writes_enabled"] is False
    before = repo.get_stock("A1001").quantity
    response = await reader.post("/api/chat/stream", json={"session_id": sid, "message": "批准"})
    assert "event: done" in response.text and repo.get_stock("A1001").quantity == before
    assert "approval_pending" not in response.text
    draft = (
        await reader.post(
            f"/api/sessions/{sid}/order-drafts",
            json={
                "customer": "reader",
                "items": [{"sku": "A1001", "quantity": 2}],
            },
        )
    ).json()
    denied = await reader.post(
        f"/api/sessions/{sid}/order-drafts/{draft['draft_id']}/confirm/stream",
        json={"expected_revision": 1},
    )
    assert denied.status_code == 403 and repo.count_orders(customer="reader") == 0
    assert (
        await reader.post(
            "/api/chat/approve", json={"session_id": sid, "pending_id": "fake", "approved": True}
        )
    ).status_code == 403
    assert not (await reader.get(f"/api/sessions/{sid}/state")).json()["pending_approvals"]
    assert any(
        r["action"] == "access_denied"
        for r in (await reader.get("/api/auth/audit")).json()["items"]
    )
    assert demo["MODEL"] == "scripted-demo"


def test_login_revocation_expiry_persistence_throttling_and_password_salts(tmp_path):
    now = [1000.0]
    store = AuthStore(tmp_path / "auth.db", ttl=60, clock=lambda: now[0])
    store.add_user("alice", PASSWORD)
    store.add_user("bobby", PASSWORD)
    with store.connect() as db:
        rows = db.execute("SELECT password_hash,salt FROM auth_user").fetchall()
    assert rows[0][0] != rows[1][0] and rows[0][1] != rows[1][1]
    identity, token, _ = store.login("alice", PASSWORD)
    restored = AuthStore(store.path, ttl=60, clock=lambda: now[0])
    assert restored.authenticate(token)[0] == identity
    now[0] += 61
    with pytest.raises(AuthError, match="失效"):
        restored.authenticate(token)
    _, token, _ = store.login("alice", PASSWORD)
    store.disable_user("alice")
    with pytest.raises(AuthError):
        store.authenticate(token)
    for _ in range(5):
        with pytest.raises(AuthError) as caught:
            store.login("unknown", "wrong password")
        assert caught.value.status == 401
    with pytest.raises(AuthError) as caught:
        store.login("unknown", "wrong password")
    assert caught.value.status == 429
    now[0] += 601
    with pytest.raises(AuthError) as caught:
        store.login("unknown", "wrong password")
    assert caught.value.status == 401

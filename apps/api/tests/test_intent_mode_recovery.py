"""Mode changes against real graph checkpoint bytes; no provider or PostgreSQL claims."""

import copy
import json
from itertools import product

import httpx2
import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import ToolCall
from agent_core.runtime_config import LoopConfig
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.intent_state import PendingIntentRow
from erpilot_api.main import create_app
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from test_draft_confirmation_api import suspend

SOURCE = "给客户甲准备订单：标准耳机两件；预算不超过1000元；只保存草稿"


def no_model():
    raise AssertionError("mode recovery must not initialize a model")


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "erp.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku in ["R1", "R2"]:
            session.add(
                ProductRow(sku=sku, name="标准耳机", price=50, category="测试", status="在售")
            )
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    yield path, engine
    engine.dispose()


async def host(path, tmp_path, mode, saver):
    p = await SessionErpTools.create(path, writes=True, gate=StreamApprovalGate(), intent_mode=mode)
    app = create_app(
        client_factory=no_model, session_erp_tools=p, checkpointer=saver, trace_dir=tmp_path / mode
    )
    return app, p


def checkpoint_copy(saver):
    # Copy only checkpoint serializer bytes, not live graphs, validators or closures.
    restored = InMemorySaver()
    for name in ("storage", "writes", "blobs"):
        setattr(restored, name, copy.deepcopy(getattr(saver, name)))
    return restored


@pytest.mark.parametrize(
    "rollback,reply",
    list(product(["off", "shadow"], ["选第二个", "四件", "预算改为1500元", "确认"])),
)
async def test_unconsumed_request_is_suspended_and_short_reply_cannot_continue(
    tmp_path,
    database,
    rollback,
    reply,
):
    path, engine = database
    saver = InMemorySaver()
    original, p = await host(path, tmp_path, "enforce", saver)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(original), base_url="http://test"
    ) as c:
        response = await c.post("/api/chat/stream", json={"session_id": "s", "message": SOURCE})
        assert "event: done" in response.text
    accepted = p.intent_analysis.store.current("s")
    app, fallback = await host(path, tmp_path, rollback, checkpoint_copy(saver))
    with Session(engine) as session:
        row = session.get(PendingIntentRow, accepted["pending_id"])
        assert row.status == "needs_recheck" and row.version == accepted["state_version"] + 1
        assert row.expires_at == accepted["expires_at"]
        assert json.loads(row.payload)["messages"] == accepted["messages"]
    before = snapshot_all(engine)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://test") as c:
        preview = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
        assert preview["status"] == "needs_recheck" and not preview["can_confirm"]
        state = (await c.get("/api/sessions/s/state")).json()
        assert state["pending_intent"]["status"] == "needs_recheck"
        response = await c.post("/api/chat/stream", json={"session_id": "s", "message": reply})
        assert "event: done" in response.text and "重新说明完整要求" in response.text
        assert (
            "event: approval_pending" not in response.text and "event: error" not in response.text
        )
    assert snapshot_all(engine) == before
    # Returning to enforce does not reactivate the old version or consume a short reply.
    restored, current = await host(
        path, tmp_path, "enforce", checkpoint_copy(app.state.service._checkpointer)
    )
    assert current.intent_analysis.store.current("s") is None
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(restored), base_url="http://test"
    ) as c:
        response = await c.post("/api/chat/stream", json={"session_id": "s", "message": reply})
        assert "event: done" in response.text and "重新说明完整要求" in response.text
    assert snapshot_all(engine) == before


@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_legacy_database_gets_no_intent_tables_and_no_recovery_capability(
    tmp_path,
    database,
    mode,
):
    path, engine = database
    before = set(inspect(engine).get_table_names())
    app, p = await host(path, tmp_path, mode, InMemorySaver())
    assert set(inspect(engine).get_table_names()) == before
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://test") as c:
        runtime = (await c.get("/api/runtime")).json()
        assert not runtime.get("intent_recovery_enabled")
        assert (await c.get("/api/sessions/s/pending-intent/preview")).status_code == 503


@pytest.mark.parametrize(
    "before_mode,after_mode,approved",
    [
        (*modes, approved)
        for modes in product(["off", "shadow", "enforce"], repeat=2)
        for approved in [False, True]
    ],
)
async def test_legacy_approval_checkpoint_keeps_exact_arguments_across_modes(
    tmp_path,
    database,
    before_mode,
    after_mode,
    approved,
):
    path, engine = database
    saver = InMemorySaver()
    app, p = await host(path, tmp_path / "before", before_mode, saver)
    draft = p.drafts.prepare(
        "s1", "旧客户", [("R1", 2)], "旧备注", evidence={"contract": "draft-form-1"}
    )
    pending = await suspend(app, draft)
    before = snapshot_all(engine)
    restored, p2 = await host(path, tmp_path / "after", after_mode, checkpoint_copy(saver))
    state = await restored.state.service.session_state("s1")
    actual = state["pending_approvals"][0]
    assert actual["pending_id"] == pending.pending_id
    assert actual["arguments"] == pending.arguments
    assert actual["expires_at"] == pending.expires_at
    assert snapshot_all(engine) == before
    events = [
        e async for e in restored.state.service.stream_resume("s1", pending.pending_id, approved)
    ]
    assert events[-1].completed
    after = snapshot_all(engine)
    assert len(after["orders"]) == (1 if approved else 0)
    assert after["stocks"][0]["quantity"] == before["stocks"][0]["quantity"] - (
        2 if approved else 0
    )
    assert p2.drafts.get("s1", draft["draft_id"])["revision"] == 1


@pytest.mark.parametrize("after_mode", ["off", "shadow", "enforce"])
async def test_saved_new_contract_is_readable_and_submittable_after_mode_change(
    tmp_path,
    database,
    after_mode,
):
    path, engine = database
    saver = InMemorySaver()
    app, p = await host(path, tmp_path / "before", "enforce", saver)
    text = "给客户甲准备订单：SKU R1 两件；只保存草稿"
    resolved = p.intent_analysis.resolve("s1", text, turn_id="create")
    args = {k: resolved[k] for k in ("customer", "note", "items", "budget_limit")}
    tools = {t.name: t for t in p.for_session("s1")}
    history = [{"name": "resolve_order_items", "ok": True, "content": json.dumps(resolved)}]
    [call] = prepare_tool_calls(
        [ToolCall("create", "prepare_order_draft", json.dumps(args))],
        tools,
        messages=[{"role": "user", "content": text}],
        tool_history=history,
    )
    result = await execute_tool_call(call, tools, LoopConfig())
    assert result["ok"], result
    draft = json.loads(result["content"])
    assert (
        p.drafts.history("s1", draft["draft_id"])["items"][0]["change_record"]["source"]["contract"]
        == "draft-intent-2"
    )
    pending = await suspend(app, draft)
    restored, p2 = await host(path, tmp_path / "after", after_mode, checkpoint_copy(saver))
    assert p2.drafts.get("s1", draft["draft_id"])["customer"] == "客户甲"
    events = [e async for e in restored.state.service.stream_resume("s1", pending.pending_id, True)]
    assert events[-1].completed and len(snapshot_all(engine)["orders"]) == 1


async def test_full_new_request_replaces_suspended_context_without_reviving_old_confirmation(
    tmp_path,
    database,
):
    path, engine = database
    app, p = await host(path, tmp_path / "before", "enforce", InMemorySaver())
    p.intent_analysis.resolve("s", SOURCE, turn_id="old")
    old = p.intent_analysis.store.current("s")
    await host(path, tmp_path / "rollback", "off", InMemorySaver())
    app, p = await host(path, tmp_path / "return", "enforce", InMemorySaver())
    text = "给新客户准备订单：SKU R1 三件；只保存草稿"
    resolved = p.intent_analysis.resolve("s", text, turn_id="new")
    assert resolved["status"] == "resolved"
    assert resolved["pending_ref"]["pending_id"] != old["pending_id"]
    assert resolved["customer"] == "新客户" and resolved["budget_limit"] is None
    body = {k: resolved[k] for k in ("customer", "note", "budget_limit", "items", "pending_ref")}
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://test") as c:
        stale = {
            **body,
            "pending_ref": {"pending_id": old["pending_id"], "state_version": old["state_version"]},
        }
        assert (
            await c.post("/api/sessions/s/pending-intent/confirm", json=stale)
        ).status_code == 409
        saved = await c.post("/api/sessions/s/pending-intent/confirm", json=body)
        assert saved.status_code == 201 and saved.json()["items"][0]["quantity"] == 3
        assert (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"] is None

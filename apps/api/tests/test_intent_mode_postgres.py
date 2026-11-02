"""Real PostgreSQL reconnects across mode changes; isolated schemas, scripted/no model."""

import json
from itertools import product

import httpx2
import pytest
from erpilot_api.checkpoint import open_checkpointer
from evals.context_workflow import snapshot_all
from test_draft_confirmation_api import suspend
from test_intent_mode_recovery import SOURCE, host
from test_intent_mode_recovery import database as database


@pytest.mark.parametrize(
    "before_mode,after_mode,approved",
    [
        (*modes, approved)
        for modes in product(["off", "shadow", "enforce"], repeat=2)
        for approved in [False, True]
    ],
)
async def test_postgres_approval_reconnect_keeps_original_contract(
    tmp_path,
    database,
    isolated_postgres_url,
    before_mode,
    after_mode,
    approved,
):
    path, engine = database
    async with open_checkpointer(isolated_postgres_url) as saver:
        app, p = await host(path, tmp_path / "before", before_mode, saver)
        draft = p.drafts.prepare(
            "s1", "旧客户", [("R1", 2)], "旧备注", evidence={"contract": "draft-form-1"}
        )
        pending = await suspend(app, draft)
        before = snapshot_all(engine)
    async with open_checkpointer(isolated_postgres_url) as saver:
        restored, p2 = await host(path, tmp_path / "after", after_mode, saver)
        state = await restored.state.service.session_state("s1")
        actual = state["pending_approvals"][0]
        assert actual["pending_id"] == pending.pending_id
        assert actual["arguments"] == pending.arguments
        assert actual["expires_at"] == pending.expires_at
        assert snapshot_all(engine) == before
        events = [
            e
            async for e in restored.state.service.stream_resume("s1", pending.pending_id, approved)
        ]
        assert events[-1].completed
        after = snapshot_all(engine)
        assert len(after["orders"]) == (1 if approved else 0)
        assert after["stocks"][0]["quantity"] == before["stocks"][0]["quantity"] - (
            2 if approved else 0
        )
        assert p2.drafts.get("s1", draft["draft_id"])["revision"] == 1


@pytest.mark.parametrize(
    "rollback,reply",
    list(product(["off", "shadow"], ["选第二个", "四件", "预算改为1500元", "确认"])),
)
async def test_postgres_pending_request_remains_suspended_after_round_trip(
    tmp_path,
    database,
    isolated_postgres_url,
    rollback,
    reply,
):
    path, engine = database
    async with open_checkpointer(isolated_postgres_url) as saver:
        app, p = await host(path, tmp_path / "before", "enforce", saver)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            response = await c.post("/api/chat/stream", json={"session_id": "s", "message": SOURCE})
            assert "event: done" in response.text
        original = p.intent_analysis.store.current("s")
    async with open_checkpointer(isolated_postgres_url) as saver:
        app, p = await host(path, tmp_path / "rollback", rollback, saver)
        before = snapshot_all(engine)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            state = (await c.get("/api/sessions/s/state")).json()
            recovery = state["pending_intent"]
            assert recovery["status"] == "needs_recheck"
            assert recovery["state_version"] == original["state_version"] + 1
            assert recovery["expires_at"] == original["expires_at"]
            response = await c.post("/api/chat/stream", json={"session_id": "s", "message": reply})
            assert "event: done" in response.text and "重新说明完整要求" in response.text
        assert snapshot_all(engine) == before
    async with open_checkpointer(isolated_postgres_url) as saver:
        app, p = await host(path, tmp_path / "return", "enforce", saver)
        assert p.intent_analysis.store.current("s") is None
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            response = await c.post("/api/chat/stream", json={"session_id": "s", "message": reply})
            assert "event: done" in response.text and "重新说明完整要求" in response.text
        assert snapshot_all(engine) == before


@pytest.mark.parametrize("after_mode", ["off", "shadow", "enforce"])
async def test_postgres_saved_intent_contract_survives_reconnect_and_submission(
    tmp_path,
    database,
    isolated_postgres_url,
    after_mode,
):
    from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
    from agent_core.llm import ToolCall
    from agent_core.runtime_config import LoopConfig

    path, engine = database
    async with open_checkpointer(isolated_postgres_url) as saver:
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
        assert result["ok"]
        draft = json.loads(result["content"])
        pending = await suspend(app, draft)
    async with open_checkpointer(isolated_postgres_url) as saver:
        app, p = await host(path, tmp_path / "after", after_mode, saver)
        record = p.drafts.history("s1", draft["draft_id"])["items"][0]["change_record"]
        assert record["source"]["contract"] == "draft-intent-2"
        events = [e async for e in app.state.service.stream_resume("s1", pending.pending_id, True)]
        assert events[-1].completed and len(snapshot_all(engine)["orders"]) == 1

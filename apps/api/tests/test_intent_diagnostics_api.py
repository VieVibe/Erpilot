import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.llm import StreamEnd, TextDelta, ToolCall, Usage
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.intent_contract import digest
from erpilot_api.main import create_app
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session

SOURCE = "给客户甲准备订单：标准耳机；备注原文『不要提交😀』；只保存草稿"


def records(directory):
    return [[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
            for p in sorted(directory.glob("*.intent.jsonl"))]


async def host(tmp_path, mode, model, *, writes=False):
    path = tmp_path / "diagnostics.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku in ("R1", "R2"):
            session.add(ProductRow(sku=sku, name="标准耳机", category="测试", price=10,
                                   status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    gate = StreamApprovalGate() if writes else None
    provider = await SessionErpTools.create(path, intent_mode=mode, writes=writes, gate=gate)
    app = create_app(client_factory=lambda: model, session_erp_tools=provider,
                     checkpointer=InMemorySaver(), trace_dir=tmp_path / "traces",
                     scripted_demo=True, approval_gate=gate)
    return app, provider, engine


class TextModel:
    async def stream_chat(self, messages, tools=None):
        yield TextDelta("你好")
        yield StreamEnd(Usage(2, 1, 3))


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
async def test_every_plain_text_turn_is_observed_only_when_enabled(tmp_path, mode):
    app, _, engine = await host(tmp_path, mode, TextModel())
    before = snapshot_all(engine)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            for _ in range(2):
                response = await client.post("/api/chat/stream",
                                             json={"session_id": "s", "message": "你好😀"})
                assert "event: done" in response.text
            # Restoration remains strictly read only and does not create a diagnostic turn.
            paths = records(tmp_path / "traces")
            await client.get("/api/sessions/s/state")
            assert records(tmp_path / "traces") == paths
        assert snapshot_all(engine) == before
        if mode == "off":
            assert paths == []
            return
        assert len(paths) == 2 and len({r[0]["request_id"] for r in paths}) == 2
        assert len({r[0]["data"]["turn_id"] for r in paths}) == 2
        for rows in paths:
            assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
            assert all(r["mode"] == mode and r["session_id"] == "s" for r in rows)
            start, end = rows[0]["data"], rows[-1]["data"]
            assert start["current_source"] == {"text": "你好😀", "sha256": digest("你好😀")}
            assert all(start[k] for k in ("parser_version", "policy_version", "prompt_sha256"))
            assert end["outcome"] == "completed" and end["business_model_requests"] == 1
            assert end["unknown_usage_steps"] == [] and end["intent_model_requests"] == 0
            assert list(end["usage_by_step"].values()) == [
                {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}]
            assert not any(r["event"] == "tool_attempt" for r in rows)
    finally:
        engine.dispose()


async def test_required_reads_rejected_attempts_and_actual_saved_version_are_distinct(tmp_path):
    class Model:
        calls = 0

        async def stream_chat(self, messages, tools=None):
            self.calls += 1
            resolved = next(json.loads(m["content"]) for m in reversed(messages)
                            if m.get("role") == "tool" and '"pending_ref"' in m["content"])
            args = {k: resolved[k] for k in ("customer", "note", "items", "budget_limit")}
            if self.calls == 1:
                yield ToolCall("forbidden", "submit_order_draft", "{}")
            elif self.calls == 2:
                args["items"] = [{"sku": "R2", "quantity": 999}]
                yield ToolCall("wrong-count", "prepare_order_draft", json.dumps(args))
            elif self.calls == 3:
                yield ToolCall("correct", "prepare_order_draft", json.dumps(args))
            else:
                yield TextDelta("草稿已保存")
            yield StreamEnd(Usage(2, 1, 3))

    model = Model()
    app, provider, engine = await host(tmp_path, "enforce", model, writes=True)
    before = snapshot_all(engine)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            for text in (SOURCE, "选第二个，三件"):
                response = await client.post("/api/chat/stream",
                                             json={"session_id": "s", "message": text})
                assert "event: done" in response.text
                assert "event: approval_pending" not in response.text
        runs = records(tmp_path / "traces")
        initial = next(r for r in runs if r[0]["data"]["current_source"]["text"] == SOURCE)
        assert initial[-1]["data"]["business_model_requests"] == 0
        assert any(r["event"] == "rule_resolution" and r["data"]["decision"] == "clarify"
                   and r["data"]["unresolved"] for r in initial)
        final = next(r for r in runs if r is not initial)
        attempts = [r["data"] for r in final if r["event"] == "tool_attempt"]
        executing = [r["data"]["call_id"] for r in final if r["event"] == "tool_executing"]
        results = {r["data"]["call_id"]: r["data"] for r in final if r["event"] == "tool_result"}
        assert {"forbidden", "wrong-count", "correct"} <= {r["id"] for r in attempts}
        assert "forbidden" not in executing and "wrong-count" not in executing
        assert "correct" in executing
        assert results["forbidden"]["rejection_code"] == "intent_stage_blocked"
        assert results["wrong-count"]["rejection_code"] and not results["wrong-count"]["ok"]
        assert any(r["event"] == "intent_validation" and not r["data"]["accepted"] for r in final)
        assert any(r["event"] == "stage_permissions"
                   and "submit_order_draft" in r["data"]["blocked_tools"] for r in final)
        draft = provider.drafts.list_latest("s")[0]
        assert final[-1]["data"]["final_versions"] == [
            {"draft_id": draft["draft_id"], "revision": 1}]
        assert final[-1]["data"]["business_model_requests"] == model.calls == 4
        after = snapshot_all(engine)
        assert before["stocks"] == after["stocks"] and after["orders"] == []
    finally:
        engine.dispose()


async def test_concurrent_sessions_have_separate_original_inputs(tmp_path):
    app, _, engine = await host(tmp_path, "enforce", TextModel())
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            responses = await asyncio.gather(*[client.post("/api/chat/stream", json={
                "session_id": f"s{i}", "message": SOURCE.replace("客户甲", f"客户{i}"),
            }) for i in range(8)])
            assert all("event: done" in r.text for r in responses)
        runs = records(tmp_path / "traces")
        assert len(runs) == 8 and len({r[0]["request_id"] for r in runs}) == 8
        for rows in runs:
            sid = rows[0]["session_id"]
            assert all(r["session_id"] == sid for r in rows)
            assert f"客户{sid[1:]}" in rows[0]["data"]["current_source"]["text"]
            assert rows[-1]["data"]["business_model_requests"] == 0
    finally:
        engine.dispose()


async def test_model_failure_preserves_unknown_usage_and_completed_prefix(tmp_path):
    class Broken:
        async def stream_chat(self, messages, tools=None):
            raise RuntimeError("scripted failure")
            yield  # pragma: no cover

    app, _, engine = await host(tmp_path, "enforce", Broken())
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            response = await client.post("/api/chat/stream",
                                         json={"session_id": "s", "message": "你好"})
            assert "event: error" in response.text
        [rows] = records(tmp_path / "traces")
        assert rows[-1]["event"] == "request_end"
        end = rows[-1]["data"]
        assert end["outcome"] == "error" and end["error_type"] == "RuntimeError"
        assert end["business_model_requests"] == 1 and end["unknown_usage_steps"] == [1]
        assert end["usage_by_step"] == {}
    finally:
        engine.dispose()


async def test_failed_diagnostic_file_does_not_change_business_result(
    tmp_path, monkeypatch, caplog,
):
    app, provider, engine = await host(tmp_path, "enforce", TextModel())
    original = Path.open

    def broken(path, *args, **kwargs):
        if str(path).endswith(".intent.jsonl"):
            raise OSError("scripted diagnostic disk failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            response = await client.post("/api/chat/stream",
                                         json={"session_id": "s", "message": SOURCE})
            assert "event: done" in response.text
        assert provider.intent_analysis.store.current("s")["status"] == "pending"
        assert "intent_diagnostics_unavailable" in caplog.text
        assert provider.intent_analysis.diagnostics._active == {}
    finally:
        engine.dispose()


@pytest.mark.parametrize("reply", ["三箱", "2包", "四盒", "五袋", "六套", "七瓶",
                                  "三箱，预算改为9000元"])
async def test_packaging_answer_keeps_pending_source_version_and_does_not_apply_scalar(
    tmp_path, reply,
):
    app, provider, engine = await host(tmp_path, "enforce", TextModel())
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            for text in (SOURCE, "选第二个"):
                response = await client.post("/api/chat/stream",
                                             json={"session_id": "s", "message": text})
                assert "event: done" in response.text
            before = provider.intent_analysis.store.current("s")
            business = snapshot_all(engine)
            response = await client.post("/api/chat/stream",
                                         json={"session_id": "s", "message": reply})
            assert "event: done" in response.text
            assert provider.intent_analysis.store.current("s") == before
            assert snapshot_all(engine) == business
            assert "最终件数" in (await client.get("/api/sessions/s/state")).text
        run = next(r for r in records(tmp_path / "traces")
                   if r[0]["data"]["current_source"]["text"] == reply)
        assert run[-1]["data"]["business_model_requests"] == 0
        assert run[-1]["data"]["rule_analysis_status"] == "observed"
    finally:
        engine.dispose()


async def test_shadow_analysis_is_persisted_but_old_validator_still_rejects(tmp_path):
    class Model:
        calls = 0

        async def stream_chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                yield ToolCall("attempt", "prepare_order_draft", json.dumps({
                    "customer": "客户甲", "items": [{"sku": "R1", "quantity": 2}],
                }))
            else:
                yield TextDelta("请先核对")
            yield StreamEnd(Usage(2, 1, 3))

    app, _, engine = await host(tmp_path, "shadow", Model())
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as client:
            response = await client.post("/api/chat/stream", json={
                "session_id": "s", "message": "客户改为客户甲",
            })
            assert "event: done" in response.text
        [rows] = records(tmp_path / "traces")
        assert any(r["event"] == "shadow_analysis" for r in rows)
        assert any(r["event"] == "tool_result" and not r["data"]["ok"] for r in rows)
        assert not any(r["event"] == "tool_executing" for r in rows)
        assert snapshot_all(engine)["order_drafts"] == []
    finally:
        engine.dispose()

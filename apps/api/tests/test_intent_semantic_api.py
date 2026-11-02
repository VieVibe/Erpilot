import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import httpx2
import pytest
from agent_core.billing import EndpointRate
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from agent_core.testing import make_client
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.intent_contract import digest
from erp_store.intent_semantic_state import IntentRequestRow, SemanticAttemptRow
from erp_store.intent_state import PendingIntentRow
from erpilot_api.main import create_app
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.intent_semantic import SemanticSettings
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy import select
from sqlalchemy.orm import Session

SOURCE = "帮客户甲列份采购清单：机械键盘要四个，预算1500元，备注『不要提交😀』。"
RATE = EndpointRate("offline", "fixture.invalid", "USD", Decimal("1"), Decimal("2"),
                    price_source="offline-fixture")
SETTINGS = SemanticSettings(RATE, "offline-semantic-1", Decimal("1"), 20, 1)


def span(message, quote):
    start = message["text"].index(quote)
    return {"message_id": message["message_id"], "start": start, "end": start + len(quote),
            "quote": quote}


def proposal(message):
    def text(quote):
        return {"value": quote, "evidence": span(message, quote)}
    return {"schema_version": 1, "operation": "create", "action": span(message, "列"),
            "customer": text("客户甲"), "note": text("不要提交😀"),
            "budget_limit": {"value": "1500", "evidence": span(message, "1500元")},
            "items": [{"product": text("机械键盘"),
                       "quantity": {"value": 4, "evidence": span(message, "四个")}}],
            "unresolved": []}


class Model:
    config = LLMConfig("offline-key", model="offline", base_url="http://fixture.invalid/v1")

    def __init__(self, behavior="valid"):
        self.behavior, self.calls, self.closed = behavior, [], 0

    async def stream_chat(self, messages, tools=None):
        self.calls.append(deepcopy(messages))
        try:
            if messages[-1].get("role") != "user" or '"message_id"' not in messages[-1]["content"]:
                yield TextDelta("请核对草稿")
                yield StreamEnd(Usage(11, 3, 14))
                return
            message = json.loads(messages[-1]["content"])
            data = proposal(message)
            if self.behavior == "timeout":
                await asyncio.sleep(10)
            elif self.behavior == "failure":
                raise RuntimeError("scripted provider failure")
            elif self.behavior == "extra":
                data["allowed_next_tools"] = ["create_order"]
            elif self.behavior == "invented":
                data["items"][0]["quantity"]["value"] = 40
            elif self.behavior == "bad_span":
                data["note"]["evidence"]["end"] -= 1
            elif self.behavior == "foreign_source":
                data["customer"]["evidence"]["message_id"] = "invented"
            elif self.behavior == "quoted_product":
                data["items"][0]["product"] = {"value": "不要提交😀",
                                                 "evidence": span(message, "不要提交😀")}
                data["note"] = None
            if self.behavior == "tool":
                yield ToolCall("invented", "create_order", "{}")
            raw = json.dumps(data, ensure_ascii=False)
            if self.behavior == "malformed":
                raw = "not json"
            elif self.behavior == "fenced":
                raw = "```json\n" + raw + "\n```"
            elif self.behavior == "duplicate":
                raw = raw[:-1] + ',"operation":"create"}'
            elif self.behavior == "oversized":
                raw = "x" * 64001
            elif self.behavior == "nonfinite":
                raw = raw.replace('"value": 4', '"value": NaN')
            elif self.behavior == "boolean":
                raw = raw.replace('"value": 4', '"value": true')
            yield TextDelta(raw)
            if self.behavior == "missing_usage":
                yield StreamEnd(None)
            elif self.behavior == "invalid_usage":
                yield StreamEnd(Usage(-1, 2, 1))
            else:
                yield StreamEnd(Usage(11, 3, 14))
        finally:
            self.closed += 1


async def host(tmp_path, model=None, *, settings=SETTINGS, existing=False, mode="enforce"):
    path = tmp_path / "semantic.db"
    engine = make_engine(path)
    if not existing:
        init_db(engine)
        with Session(engine) as session:
            session.add(ProductRow(sku="K1", name="机械键盘", category="测试", price=200,
                                   status="在售"))
            session.add(StockRow(sku="K1", quantity=1000, warehouse="主仓"))
            session.commit()
    provider = await SessionErpTools.create(path, intent_mode=mode)
    model = model or Model()
    factory_calls = []
    def factory():
        factory_calls.append(True)
        return model
    app = create_app(client_factory=factory, model="offline", session_erp_tools=provider,
                     intent_semantic_settings=settings, checkpointer=InMemorySaver(),
                     trace_dir=tmp_path / "traces", scripted_demo=True)
    return app, provider, engine, model, factory_calls


def journal(engine, cls=IntentRequestRow):
    with Session(engine) as session:
        return [json.loads(row.payload) for row in session.scalars(select(cls))]


async def chat(client, text=SOURCE, sid="s"):
    response = await client.post("/api/chat/stream", json={"session_id": sid, "message": text})
    assert response.status_code == 200
    return response


def form(preview):
    return {"pending_ref": {k: preview[k] for k in ("pending_id", "state_version")},
            "customer": "完整表单客户", "note": "表单核对后的备注", "budget_limit": 1600,
            "items": [{"sku": "K1", "quantity": 5}]}


async def test_free_creation_requires_full_form_and_persists_actual_source_and_native_billing(
    tmp_path,
):
    app, provider, engine, model, _ = await host(tmp_path)
    before = snapshot_all(engine)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            response = await chat(c)
            assert "event: done" in response.text and "event: approval_pending" not in response.text
            assert snapshot_all(engine) == before and len(model.calls) == 1
            preview = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
            assert preview["origin"] == "semantic_proposal" and preview["source_text"] == SOURCE
            assert preview["can_confirm"] and preview["customer"] == "客户甲"
            assert preview["items"][0]["quantity"] == 4 and preview["items"][0]["sku"] == "K1"
            assert preview["note"] == "不要提交😀" and preview["budget_limit"] == 1500
            assert "帮" in preview["unmapped_text"]
            pending = provider.intent_analysis.store.current("s")
            for reply in ("选第一个", "四件", "预算改为2000元"):
                assert "event: done" in (await chat(c, reply)).text
                assert provider.intent_analysis.store.current("s") == pending
                assert len(model.calls) == 1
            saved = await c.post("/api/sessions/s/pending-intent/confirm", json=form(preview))
            assert saved.status_code == 201
            draft = saved.json()
            assert draft["items"][0]["quantity"] == 5 and draft["budget_limit"] == 1600
            assert len(model.calls) == 1
            assert (await c.post("/api/sessions/s/pending-intent/confirm",
                                 json=form(preview))).status_code == 409
        history = provider.drafts.history("s", draft["draft_id"])["items"][0]["change_record"]
        assert history["source"]["kind"] == "manual_form"
        context = history["source"]["intent_preview"]
        assert context["messages"][0]["text"] == SOURCE
        assert context["messages"][0]["sha256"] == digest(SOURCE)
        assert context["verification"] == "explicit_form"
        assert context["semantic_proposal"]["items"][0]["quantity"]["value"] == 4
        assert snapshot_all(engine)["stocks"] == before["stocks"]
        assert snapshot_all(engine)["orders"] == []
        [request] = journal(engine)
        assert request["purpose"] == "intent" and request["status"] == "settled"
        assert request["billing"]["currency"] == "USD" and request["billing"]["complete"]
        assert request["usage"] == {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
        rows = [json.loads(line) for p in (tmp_path / "traces").glob("*.intent.jsonl")
                for line in p.read_text(encoding="utf-8").splitlines()]
        ends = [r["data"] for r in rows if r["event"] == "request_end"]
        assert sum(r["intent_model_requests"] for r in ends) == 1
        assert all(r["business_model_requests"] == 0 for r in ends)
        assert any(r["metered_known_amounts"] == {"USD": "0.000017"} for r in ends)
    finally:
        engine.dispose()


@pytest.mark.parametrize("behavior", ["malformed", "fenced", "duplicate", "extra", "invented",
    "bad_span", "foreign_source", "quoted_product", "tool", "missing_usage", "invalid_usage",
    "failure", "oversized", "timeout", "nonfinite", "boolean"])
async def test_proposal_faults_make_one_request_no_preview_no_business_write(tmp_path, behavior):
    settings = replace(SETTINGS, timeout_seconds=.03 if behavior == "timeout" else 1)
    app, provider, engine, model, _ = await host(tmp_path, Model(behavior), settings=settings)
    before = snapshot_all(engine)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            response = await chat(c)
            assert "event: done" in response.text
            assert len(model.calls) == model.closed == 1
            assert provider.intent_analysis.store.current("s") is None
            assert snapshot_all(engine) == before
        [attempt] = journal(engine, SemanticAttemptRow)
        assert attempt["status"] == "rejected"
        unknown = behavior in {"timeout", "failure", "oversized", "missing_usage", "invalid_usage"}
        assert provider.intent_analysis.semantic.budget.unknown_usage == unknown
    finally:
        engine.dispose()


@pytest.mark.parametrize("prefix", ["如果库存够就", "不要", "只查询", "是否可以", "先别"])
async def test_known_scope_barriers_do_not_invoke_semantic_model(tmp_path, prefix):
    app, provider, engine, model, factories = await host(tmp_path)
    try:
        # Invoke the application resolver directly to isolate its fallback decision.
        result = provider.intent_analysis.resolve("s", prefix + SOURCE, turn_id="scope")
        await provider.intent_analysis.semantic.propose("s", prefix + SOURCE, "scope", result)
        assert model.calls == [] and factories == [] and journal(engine) == []
        assert snapshot_all(engine)["order_drafts"] == []
    finally:
        engine.dispose()


async def test_rule_fast_path_and_business_requests_share_same_persisted_budget(tmp_path):
    app, provider, engine, model, _ = await host(
        tmp_path, settings=replace(SETTINGS, max_requests=2))
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            assert "event: done" in (await chat(c)).text
            assert "event: done" in (await chat(c, "你好")).text
            assert "event: error" in (await chat(c, "再次问好")).text
        assert len(model.calls) == 2
        requests = journal(engine)
        assert [r["purpose"] for r in requests] == ["intent", "business"]
        assert provider.intent_analysis.semantic.budget.spent == Decimal("0.000034")
    finally:
        engine.dispose()


async def test_rule_supported_creation_skips_extra_proposal_request(tmp_path):
    app, _, engine, model, _ = await host(tmp_path)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            response = await chat(c, "给客户甲准备订单：机械键盘三件；只保存草稿")
            assert "event: done" in response.text
        assert len(model.calls) == 1 and journal(engine)[0]["purpose"] == "business"
        assert journal(engine, SemanticAttemptRow) == []
    finally:
        engine.dispose()


async def test_unknown_usage_and_attempt_dedup_survive_host_reconstruction(tmp_path):
    app, provider, engine, model, _ = await host(tmp_path, Model("missing_usage"))
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            assert "event: done" in (await chat(c)).text
        with Session(engine) as session:
            turn_id = session.scalar(select(SemanticAttemptRow.turn_id))
        restored, p2, e2, m2, factories = await host(tmp_path, existing=True)
        try:
            assert p2.intent_analysis.semantic.budget.unknown_usage
            fallback = p2.intent_analysis.resolve("s", SOURCE, turn_id=turn_id)
            await p2.intent_analysis.semantic.propose("s", SOURCE, turn_id, fallback)
            assert factories == [] and m2.calls == []
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(restored),
                                          base_url="http://test") as c:
                assert "event: error" in (await chat(c, "你好")).text
            assert m2.calls == [] and len(model.calls) == 1 and len(journal(engine)) == 1
        finally:
            e2.dispose()
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", ["price", "name", "status", "duplicate"])
async def test_changed_catalogue_invalidates_semantic_preview_and_confirmation(tmp_path, change):
    app, provider, engine, model, _ = await host(tmp_path)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            await chat(c)
            preview = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
            with Session(engine) as session:
                product = session.get(ProductRow, "K1")
                if change == "duplicate":
                    session.add(ProductRow(sku="K2", name="机械键盘", category="测试", price=200,
                                           status="在售"))
                else:
                    setattr(product, change, {"price": 201, "name": "新键盘",
                                              "status": "下架"}[change])
                session.commit()
            current = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
            assert current["status"] == "needs_recheck" and not current["can_confirm"]
            response = await c.post("/api/sessions/s/pending-intent/confirm", json=form(preview))
            assert response.status_code == (422 if change == "status" else 409)
            assert provider.drafts.list_latest("s") == [] and len(model.calls) == 1
    finally:
        engine.dispose()


async def test_empty_budget_blocks_request_before_model_initialization_and_http(tmp_path):
    app, provider, engine, model, factories = await host(
        tmp_path, settings=replace(SETTINGS, limit=Decimal(0)))
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            assert "event: done" in (await chat(c)).text
        assert model.calls == [] and factories == [] and journal(engine) == []
        assert provider.intent_analysis.store.current("s") is None
        assert not provider.intent_analysis.semantic.budget.unknown_usage
    finally:
        engine.dispose()


async def test_configured_provider_cannot_leak_semantics_into_another_unmetered_host(tmp_path):
    _, provider, engine, model, factories = await host(tmp_path)
    try:
        with pytest.raises(ValueError, match="fresh provider"):
            create_app(client_factory=lambda: model, model="offline", session_erp_tools=provider,
                       checkpointer=InMemorySaver(), trace_dir=tmp_path / "another-host")
        assert factories == [] and model.calls == [] and journal(engine) == []
    finally:
        engine.dispose()


async def test_successful_preview_restores_without_model_and_budget_cannot_be_reset(tmp_path):
    app, provider, engine, model, _ = await host(tmp_path)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            await chat(c)
            expected = (await c.get("/api/sessions/s/pending-intent/preview")).json()
        restored, p2, e2, m2, factories = await host(tmp_path, existing=True)
        try:
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(restored),
                                          base_url="http://test") as c:
                assert (await c.get("/api/sessions/s/pending-intent/preview")).json() == expected
            assert factories == [] and m2.calls == []
            assert p2.intent_analysis.semantic.budget.requests == 1
            assert p2.intent_analysis.semantic.budget.spent == Decimal("0.000017")
            with pytest.raises(ValueError, match="预算配置不可覆盖"):
                await host(tmp_path, existing=True, settings=replace(SETTINGS, limit=Decimal(2)))
        finally:
            e2.dispose()
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_semantic_opt_in_is_rejected_outside_enforce(tmp_path, mode):
    with pytest.raises(ValueError, match="enforce"):
        await host(tmp_path, mode=mode)


async def test_wrong_endpoint_stops_without_provider_request_or_invented_charge(tmp_path):
    model = Model()
    model.config = replace(model.config, base_url="http://unconfirmed.invalid/v1")
    app, provider, engine, model, _ = await host(tmp_path, model)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            assert "event: done" in (await chat(c)).text
        assert model.calls == [] and journal(engine) == []
        assert provider.intent_analysis.store.current("s") is None
    finally:
        engine.dispose()


async def test_sdk_transport_retries_are_disabled_for_single_semantic_request(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(503, json={"error": {"message": "scripted upstream unavailable"}})
    raw = make_client(handler, max_retries=2)
    # Use its actual SDK host/model in the explicitly supplied offline rate.
    rate = replace(RATE, model="glm-5.3-flash", endpoint_host="llm.test")
    app, provider, engine, _, _ = await host(tmp_path, settings=None)
    app = create_app(client_factory=lambda: raw, model="glm-5.3-flash",
                     session_erp_tools=provider, checkpointer=InMemorySaver(),
                     trace_dir=tmp_path / "sdk-traces", scripted_demo=True,
                     intent_semantic_settings=replace(SETTINGS, rate=rate))
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            assert "event: done" in (await chat(c)).text
        assert len(requests) == 1
        assert provider.intent_analysis.semantic.budget.unknown_usage
        assert journal(engine)[0]["status"] == "unknown"
    finally:
        await raw._client.close()
        engine.dispose()


async def test_real_name_ambiguity_stays_blank_until_full_form_selects_a_sku(tmp_path):
    app, provider, engine, model, _ = await host(tmp_path)
    try:
        with Session(engine) as session:
            session.add(ProductRow(sku="K2", name="机械键盘", category="测试", price=220,
                                   status="在售"))
            session.add(StockRow(sku="K2", quantity=1000, warehouse="主仓"))
            session.commit()
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            await chat(c)
            preview = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
            assert preview["items"][0]["sku"] is None
            assert [r["sku"] for r in preview["items"][0]["candidates"]] == ["K1", "K2"]
            fields = form(preview)
            fields["items"][0]["sku"] = "K2"
            saved = await c.post("/api/sessions/s/pending-intent/confirm", json=fields)
            assert saved.status_code == 201 and saved.json()["items"][0]["sku"] == "K2"
        assert len(model.calls) == 1 and len(provider.drafts.list_latest("s")) == 1
    finally:
        engine.dispose()


async def test_source_tamper_and_foreign_session_cannot_confirm_candidates(tmp_path):
    app, provider, engine, model, _ = await host(tmp_path)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                      base_url="http://test") as c:
            await chat(c)
            preview = (await c.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
            assert (await c.post("/api/sessions/foreign/pending-intent/confirm",
                                 json=form(preview))).status_code == 409
            with Session(engine) as session:
                row = session.get(PendingIntentRow, preview["pending_id"])
                data = json.loads(row.payload)
                data["messages"][0]["text"] += " 被修改"
                row.payload = json.dumps(data, ensure_ascii=False)
                session.commit()
            assert not (await c.get("/api/sessions/s/pending-intent/preview")).json()[
                "preview"]["can_confirm"]
            assert (await c.post("/api/sessions/s/pending-intent/confirm",
                                 json=form(preview))).status_code == 409
        assert provider.drafts.list_latest("s") == [] and len(model.calls) == 1
    finally:
        engine.dispose()


async def test_async_candidate_cannot_replace_a_newer_pending_request(tmp_path):
    class Paused(Model):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def stream_chat(self, messages, tools=None):
            self.started.set()
            await self.release.wait()
            async for event in super().stream_chat(messages, tools):
                yield event

    model = Paused()
    _, provider, engine, _, _ = await host(tmp_path, model)
    coordinator = provider.intent_analysis.semantic
    try:
        fallback = provider.intent_analysis.resolve("s", SOURCE, turn_id="old")
        task = asyncio.create_task(coordinator.propose("s", SOURCE, "old", fallback))
        await asyncio.wait_for(model.started.wait(), 1)
        provider.intent_analysis.resolve(
            "s", "给新客户准备订单：机械键盘三件；只保存草稿", turn_id="new")
        newer = provider.intent_analysis.store.current("s")
        model.release.set()
        await task
        assert provider.intent_analysis.store.current("s") == newer
        assert newer["messages"][0]["message_id"] == "new"
        assert len(model.calls) == 1
        assert journal(engine, SemanticAttemptRow)[0]["status"] == "rejected"
    finally:
        model.release.set()
        engine.dispose()


async def test_cancelled_proposal_closes_stream_and_persists_unknown_usage(tmp_path):
    model = Model("timeout")
    _, provider, engine, _, _ = await host(tmp_path, model)
    coordinator = provider.intent_analysis.semantic
    try:
        fallback = provider.intent_analysis.resolve("s", SOURCE, turn_id="cancel")
        task = asyncio.create_task(coordinator.propose("s", SOURCE, "cancel", fallback))
        for _ in range(100):
            if model.calls:
                break
            await asyncio.sleep(.001)
        assert len(model.calls) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert model.closed == 1 and coordinator.budget.unknown_usage
        assert journal(engine)[0]["status"] == "unknown"
        assert journal(engine, SemanticAttemptRow)[0]["status"] == "interrupted"
        assert provider.intent_analysis.store.current("s") is None
    finally:
        engine.dispose()

"""Exercise the actual startup policy without calling an external model."""

import httpx2
import pytest
from agent_core.llm import LLMConfig
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erpilot_api.bootstrap import build_live_app, semantic_settings
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import Session
from test_intent_semantic_api import SOURCE, Model, form


@pytest.fixture(autouse=True)
def clean_semantic_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("ERPILOT_SEMANTIC") or name == "ERPILOT_INTENT_MODE":
            monkeypatch.delenv(name)
    monkeypatch.setenv("ERPILOT_WRITES", "0")


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("ERPILOT_SEMANTIC_INPUT_PER_M", "1")
    monkeypatch.setenv("ERPILOT_SEMANTIC_OUTPUT_PER_M", "2")
    monkeypatch.setenv("ERPILOT_SEMANTIC_CURRENCY", "USD")
    engine = make_engine(tmp_path / "erp.db")
    init_db(engine)
    with Session(engine) as session:
        session.add(
            ProductRow(sku="K1", name="机械键盘", category="测试", price=200, status="在售")
        )
        session.add(StockRow(sku="K1", quantity=1000, warehouse="主仓"))
        session.commit()
    yield engine
    engine.dispose()


async def build(tmp_path, model):
    return await build_live_app(
        config=model.config,
        database=tmp_path / "erp.db",
        client_factory=lambda: model,
        trace_dir=tmp_path / "traces",
        checkpointer=InMemorySaver(),
        auth_enabled=False,
    )


async def test_default_startup_previews_before_save_and_quota_never_blocks_chat(
    configured,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ERPILOT_SEMANTIC_MAX_REQUESTS", "1")
    model = Model()
    app = await build(tmp_path, model)
    assert model.calls == []  # Startup and health checks are free.
    assert app.state.service._loop_config.tool_timeouts == {"resolve_order_items": 65}
    assert app.state.service._loop_config.tool_timeout == 30
    before = snapshot_all(configured)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://test") as http,
    ):
        runtime = (await http.get("/api/runtime")).json()
        assert runtime["intent_mode"] == "enforce"
        assert runtime["intent_semantic_enabled"] and runtime["intent_semantic_available"]
        assert runtime["intent_semantic_revision_enabled"] and not runtime["writes_enabled"]
        response = await http.post("/api/chat/stream", json={"session_id": "s", "message": SOURCE})
        assert "event: done" in response.text and "approval_pending" not in response.text
        preview = (await http.get("/api/sessions/s/pending-intent/preview")).json()["preview"]
        assert preview["origin"] == "semantic_proposal" and preview["items"][0]["quantity"] == 4
        assert snapshot_all(configured) == before
        assert not (await http.get("/api/runtime")).json()["intent_semantic_available"]
        response = await http.post(
            "/api/chat/stream", json={"session_id": "general", "message": "你好"}
        )
        assert "event: done" in response.text and "event: error" not in response.text
        saved = await http.post("/api/sessions/s/pending-intent/confirm", json=form(preview))
        assert saved.status_code == 201
        assert saved.json()["items"][0]["quantity"] == 5
    budget = app.state.service._intent_semantic.budget
    assert budget.requests == 1 and len(model.calls) == 2
    assert snapshot_all(configured)["orders"] == before["orders"]
    restarted = await build(tmp_path, model)
    assert restarted.state.service._intent_semantic.budget.exhausted
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(restarted), base_url="http://test"
    ) as http:
        response = await http.post(
            "/api/chat/stream", json={"session_id": "other", "message": "你好"}
        )
        assert "event: done" in response.text and "event: error" not in response.text


@pytest.mark.parametrize("setting", ["0", "false", "no"])
async def test_explicit_switch_off_preserves_normal_service(
    configured, tmp_path, monkeypatch, setting
):
    monkeypatch.setenv("ERPILOT_SEMANTIC", setting)
    app = await build(tmp_path, Model())
    assert app.state.service._intent_semantic is None
    assert app.state.semantic_runtime["intent_mode"] == "enforce"
    assert app.state.semantic_runtime["intent_semantic_enabled"] is False


def test_unknown_price_degrades_with_actionable_reason_and_explicit_rate_is_supported(monkeypatch):
    config = LLMConfig("unused", model="new-model", base_url="https://model.invalid/v1")
    settings, reason = semantic_settings(config, "enforce")
    assert settings is None and "价目" in reason
    monkeypatch.setenv("ERPILOT_SEMANTIC_INPUT_PER_M", "0.6")
    monkeypatch.setenv("ERPILOT_SEMANTIC_OUTPUT_PER_M", "2")
    settings, reason = semantic_settings(config, "enforce")
    assert reason is None and not settings.include_business
    assert settings.rate.price_source == "operator-configuration"


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_recovery_modes_take_precedence_over_default_on(mode):
    settings, _ = semantic_settings(Model.config, mode)
    assert settings is None


def test_confirmed_endpoint_enables_without_any_extra_settings():
    config = LLMConfig("unused", model="glm-5.3-flash", base_url="https://flashcoding.ai/v1")
    settings, reason = semantic_settings(config, "enforce")
    assert reason is None and not settings.include_business
    assert settings.max_requests == 200 and settings.limit == 1


def test_mistyped_switch_fails_at_startup(monkeypatch):
    monkeypatch.setenv("ERPILOT_SEMANTIC", "enabled-ish")
    with pytest.raises(ValueError, match="ERPILOT_SEMANTIC"):
        semantic_settings(Model.config, "enforce")


async def test_unknown_usage_closes_only_candidate_budget_and_survives_restart(
    configured,
    tmp_path,
):
    model = Model("missing_usage")
    app = await build(tmp_path, model)
    before = snapshot_all(configured)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test"
    ) as http:
        response = await http.post("/api/chat/stream", json={"session_id": "s", "message": SOURCE})
        assert "event: done" in response.text
        assert (await http.get("/api/sessions/s/pending-intent/preview")).json()["preview"] is None
        response = await http.post(
            "/api/chat/stream", json={"session_id": "other", "message": "你好"}
        )
        assert "event: done" in response.text and "event: error" not in response.text
    assert snapshot_all(configured) == before
    restarted = await build(tmp_path, model)
    assert restarted.state.service._intent_semantic.budget.unknown_usage
    assert restarted.state.service._intent_semantic.budget.exhausted


@pytest.mark.parametrize("async_loader", [False, True])
def test_uvicorn_compatibility_loader_works_inside_and_outside_event_loop(
    monkeypatch, async_loader
):
    import asyncio

    from erpilot_api import bootstrap

    expected = object()

    async def fake_build():
        return expected

    monkeypatch.setattr(bootstrap, "build_live_app", fake_build)

    async def inside_loop():
        return bootstrap.default_app()

    actual = asyncio.run(inside_loop()) if async_loader else bootstrap.default_app()
    assert actual is expected

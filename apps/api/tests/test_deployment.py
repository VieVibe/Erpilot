"""Deployment initialization cannot replace real data or publish partial seed data."""

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from erpilot_api import deployment, offline


def test_initialization_preserves_existing_data_and_recovers_failed_first_seed(
    tmp_path, monkeypatch
):
    database = tmp_path / "erpilot.db"

    def broken(path):
        path.write_bytes(b"incomplete")
        raise RuntimeError("seed interrupted")

    monkeypatch.setattr(offline, "seed_demo_database", broken)
    with pytest.raises(RuntimeError, match="interrupted"):
        deployment.initialize_database(database)
    assert not database.exists() and not list(tmp_path.glob(".seed-*.db"))
    monkeypatch.setattr(
        offline, "seed_demo_database", lambda path: path.write_bytes(b"complete")
    )
    deployment.initialize_database(database)
    assert database.read_bytes() == b"complete"
    monkeypatch.setattr(offline, "seed_demo_database", broken)
    deployment.initialize_database(database)
    assert database.read_bytes() == b"complete"


def test_concurrent_seed_never_replaces_a_database_created_during_initialization(
    tmp_path, monkeypatch
):
    database = tmp_path / "erpilot.db"

    def concurrent(path):
        path.write_bytes(b"new seed")
        database.write_bytes(b"existing business data")

    monkeypatch.setattr(offline, "seed_demo_database", concurrent)
    deployment.initialize_database(database)
    assert database.read_bytes() == b"existing business data"
    assert not list(tmp_path.glob(".seed-*.db"))


async def test_default_deployment_leaves_reference_erp_database_unseeded(tmp_path, monkeypatch):
    monkeypatch.delenv("ERPILOT_RUNTIME", raising=False)
    monkeypatch.delenv("ERPILOT_MODULE", raising=False)
    monkeypatch.setenv("ERPILOT_DATA_DIR", str(tmp_path))
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setenv("ERPILOT_WEB_DIST", str(web))
    app = await deployment.build_app()
    assert app.state.deployment_mode == "live"
    assert app.state.business_module is None
    assert not (tmp_path / "erpilot.db").exists()


async def test_readiness_includes_selected_business_module_health():
    calls = []

    class Checkpointer:
        async def aget_tuple(self, config):
            calls.append(config["configurable"]["thread_id"])

    class Auth:
        @contextmanager
        def connect(self):
            database = sqlite3.connect(":memory:")
            try:
                yield database
            finally:
                database.close()

    async def module_health():
        calls.append("module")

    app = SimpleNamespace(state=SimpleNamespace(
        service=SimpleNamespace(_checkpointer=Checkpointer()),
        auth_store=Auth(), module_health_check=module_health,
    ))
    await deployment.check_dependencies(app, timeout_seconds=1)
    assert calls == ["deployment-readiness", "module"]

    async def unavailable():
        raise RuntimeError("ERP unavailable")

    app.state.module_health_check = unavailable
    with pytest.raises(RuntimeError, match="ERP unavailable"):
        await deployment.check_dependencies(app, timeout_seconds=1)

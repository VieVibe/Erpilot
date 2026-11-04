"""Deployment initialization cannot replace real data or publish partial seed data."""

import pytest
from erpilot_api import deployment


def test_initialization_preserves_existing_data_and_recovers_failed_first_seed(
    tmp_path, monkeypatch
):
    database = tmp_path / "erpilot.db"

    def broken(path):
        path.write_bytes(b"incomplete")
        raise RuntimeError("seed interrupted")

    monkeypatch.setattr(deployment, "seed_demo_database", broken)
    with pytest.raises(RuntimeError, match="interrupted"):
        deployment.initialize_database(database)
    assert not database.exists() and not list(tmp_path.glob(".seed-*.db"))
    monkeypatch.setattr(
        deployment, "seed_demo_database", lambda path: path.write_bytes(b"complete")
    )
    deployment.initialize_database(database)
    assert database.read_bytes() == b"complete"
    monkeypatch.setattr(deployment, "seed_demo_database", broken)
    deployment.initialize_database(database)
    assert database.read_bytes() == b"complete"


def test_concurrent_seed_never_replaces_a_database_created_during_initialization(
    tmp_path, monkeypatch
):
    database = tmp_path / "erpilot.db"

    def concurrent(path):
        path.write_bytes(b"new seed")
        database.write_bytes(b"existing business data")

    monkeypatch.setattr(deployment, "seed_demo_database", concurrent)
    deployment.initialize_database(database)
    assert database.read_bytes() == b"existing business data"
    assert not list(tmp_path.glob(".seed-*.db"))

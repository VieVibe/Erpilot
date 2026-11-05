"""Excluded live suites must not load credentials or change offline test behavior."""

import runpy
from pathlib import Path

import pytest
from agent_core import dotenv


@pytest.mark.parametrize("name", [
    "test_evals_live.py",
    "test_evals_write_live.py",
    "test_evals_approval_live.py",
    "test_evals_prompt_injection_live.py",
    "test_rag_live.py",
])
def test_live_module_collection_does_not_load_local_configuration(monkeypatch, name):
    def forbidden(*args, **kwargs):
        pytest.fail("collecting a live suite must not discover or load .env")

    monkeypatch.setattr(dotenv, "find_dotenv", forbidden)
    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    # Even invalid live-only settings must stay unevaluated during offline collection.
    for key in ("ERPILOT_EVAL_BUDGET", "ERPILOT_EVAL_TIMEOUT", "ERPILOT_INJECTION_VARIANT"):
        monkeypatch.setenv(key, "not-an-offline-setting")
    runpy.run_path(str(Path(__file__).with_name(name)))

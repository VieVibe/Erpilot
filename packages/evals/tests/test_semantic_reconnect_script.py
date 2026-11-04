import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "semantic_reconnect", Path(__file__).resolve().parents[3] / "scripts/semantic_reconnect.py"
)
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


@pytest.mark.parametrize(
    "code,success_after,expected_calls",
    [
        ("APIConnectionError", 1, 2),
        ("ValueError", 99, 1),
        ("TimeoutError", 99, 3),
    ],
)
def test_full_batch_retry_is_transport_only_and_keeps_original_ledgers(
    tmp_path,
    monkeypatch,
    code,
    success_after,
    expected_calls,
):
    calls, limits = [], []

    def child(command, **kwargs):
        directory = Path(command[command.index("--output") + 1])
        limits.append(command[command.index("--limit") + 1])
        directory.mkdir()
        calls.append(directory)
        passed = len(calls) > success_after
        report = {
            "passed": passed,
            "known_spent": "0.001",
            "provider_requests": 2,
            "requests": [{"status": "settled", "billing": {"complete": True}}]
            + ([] if passed else [{"status": "unknown", "billing": None}]),
            "attempts": [] if passed else [{"rejection_code": code}],
        }
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0 if passed else 1)

    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    monkeypatch.setattr(campaign.subprocess, "run", child)
    monkeypatch.setattr(campaign.time, "sleep", lambda seconds: None)
    result = campaign.run(tmp_path / "probe")
    assert len(calls) == len(result["batches"]) == expected_calls
    assert all((directory / "report.json").exists() for directory in calls)
    assert result["unknown_requests"] == (1 if success_after == 1 else expected_calls)
    assert result["passed"] == (success_after == 1)
    assert Decimal(limits[0]) == Decimal("0.02")
    if len(limits) > 1:
        assert Decimal(limits[1]) == Decimal("0.014")
    with pytest.raises(ValueError, match="exists"):
        campaign.run(tmp_path / "probe")

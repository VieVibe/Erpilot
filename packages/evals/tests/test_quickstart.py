"""Bootstrap acceptance: no installed app needed, no hidden model/service fallback."""

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "quickstart.py"
spec = importlib.util.spec_from_file_location("quickstart", SCRIPT)
quickstart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quickstart)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "fresh checkout with spaces"
    for name in ("uv.lock", "apps/web/package-lock.json", "scripts/demo.py"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture", encoding="utf-8")
    return root


def test_help_runs_outside_checkout_without_importing_app_or_reading_env(tmp_path):
    (tmp_path / ".env").write_text("this is not an environment file", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--help"], cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0
    assert all(action in result.stdout for action in ("doctor", "demo", "verify"))
    assert "只读检查" in result.stdout
    assert list(tmp_path.iterdir()) == [tmp_path / ".env"]


def test_busy_port_is_rejected_without_contacting_existing_service():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(quickstart.SetupError, match="--port"):
            quickstart.check_port(occupied.getsockname()[1])


def test_missing_uv_fails_before_installing_or_changing_checkout(checkout, monkeypatch):
    monkeypatch.setattr(quickstart, "executable", lambda name: sys.executable)
    monkeypatch.setattr(quickstart.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 0, "v22.23.3\n"))

    def missing_uv():
        raise quickstart.SetupError("缺少 uv")

    monkeypatch.setattr(quickstart, "uv_command", missing_uv)
    before = sorted(checkout.rglob("*"))
    with pytest.raises(quickstart.SetupError, match="uv"):
        quickstart.preflight(checkout, install=True)
    assert sorted(checkout.rglob("*")) == before


def test_failed_step_keeps_output_and_stops_with_original_status(tmp_path):
    records = []
    with pytest.raises(subprocess.CalledProcessError) as error:
        quickstart.run_step(
            "intentional failure", [sys.executable, "-c", "print('diagnostic'); exit(7)"],
            cwd=tmp_path, records=records, output=tmp_path,
        )
    assert error.value.returncode == 7
    assert records[0]["status"] == "failed" and records[0]["exit_code"] == 7
    assert "diagnostic" in (tmp_path / "01.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("fail", [False, True])
def test_verification_records_failure_and_never_schedules_live_evals(
    checkout, monkeypatch, fail,
):
    commands = {"python": sys.executable, "npm": "npm", "uv": ["uv"]}
    seen = []

    def fake_step(label, command, *, cwd, records, output, ci):
        assert cwd == checkout
        assert ci
        seen.append(command)
        records.append({"name": label, "status": "failed" if fail else "passed"})
        if fail:
            raise subprocess.CalledProcessError(9, command)

    monkeypatch.setattr(quickstart, "run_step", fake_step)
    monkeypatch.delenv("ERPILOT_TEST_CHECKPOINT_DATABASE_URL", raising=False)
    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            quickstart.verify(checkout, commands, install=True, browser=True)
        assert len(seen) == 1
    else:
        quickstart.verify(checkout, commands, install=True, browser=True)
        assert seen[0] == ["uv", "sync", "--all-packages", "--frozen"]
        assert "ci" in seen[1]
        pytest_command = next(cmd for cmd in seen if "pytest" in cmd)
        assert pytest_command[pytest_command.index("-m", 2) + 1] == "not eval"
        assert all("evals" not in cmd for cmd in seen)
        assert any("test:demo" in cmd for cmd in seen)
    reports = list((checkout / "artifacts/verification").glob("*/verification.json"))
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == ("failed" if fail else "passed")
    assert not report["live_model_evaluation"] and not report["postgres_configured"]
    assert len(report["lock_sha256"]["uv.lock"]) == 64

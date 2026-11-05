"""Quality gate acceptance: misleading successes, hidden regressions and unknown spend."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from evals.compare import compatibility_key
from evals.gate import GatePolicy, evaluate_gate, load_policy, validate_scope

ROOT = Path(__file__).resolve().parents[3]


def row(case_id, *, passed=True, **changes):
    return {"case_id": case_id, "passed": passed, "completed": True,
            "failed_checks": [] if passed else ["must_mention: absent"],
            "error": None, "duration_ms": 100.0, "total_tokens": 10,
            "tool_calls": [], "tool_attempts": [], "cost": 0.001,
            "cost_complete": True, **changes}


def report(*rows):
    rows = list(rows) or [row("a"), row("b")]
    return {"model": "scripted-test-model", "ts": "20261019-120000",
            "provenance": {"source_sha256": "a" * 64},
            "suite": {"case_ids": [r["case_id"] for r in rows], "planned": len(rows),
                      "executed": len(rows), "complete": True,
                      "source_changed_during_run": False,
                      "scorer_version": "test-1", "case_sha256": "b" * 64,
                      "retry_policy": "no-rerun", "seed": {"value": 1},
                      "request_timeout_seconds": 30.0}, "results": rows}


def policy(**changes):
    return GatePolicy.model_validate({"name": "test-v1", "case_ids": ["a", "b"], **changes})


def failed_codes(decision):
    return {c["code"] for c in decision["checks"] if not c["passed"]}


def test_gate_preserves_inputs_and_records_reproducible_evidence():
    payload = report()
    original = deepcopy(payload)
    decision = evaluate_gate(payload, policy())
    assert decision["passed"] and payload == original
    assert decision == evaluate_gate(payload, policy())
    assert len(decision["report_sha256"]) == len(decision["policy_sha256"]) == 64
    assert decision["metrics"]["estimated_costs"] == {"CNY": "0.002"}
    assert decision["metrics"]["p95_case_duration_ms"] == 100


@pytest.mark.parametrize("mutate", [
    lambda r: r["results"].pop(),
    lambda r: r["results"].__setitem__(1, deepcopy(r["results"][0])),
    lambda r: r["suite"].__setitem__("case_ids", ["a", "a"]),
    lambda r: r["results"].append(row("extra")),
])
def test_coverage_cannot_be_inflated_by_missing_duplicate_or_extra_results(mutate):
    payload = report()
    mutate(payload)
    assert "coverage" in failed_codes(evaluate_gate(payload, policy()))


@pytest.mark.parametrize("mutate", [
    lambda r: r["suite"].__setitem__("complete", False),
    lambda r: r["suite"].__setitem__("executed", 0),
    lambda r: r["suite"].__setitem__("source_changed_during_run", True),
    lambda r: r["results"][1].update(passed=False, failed_checks=["budget: exhausted"]),
    lambda r: r["results"][1].update(passed=False, failed_checks=["not_run: interrupted"]),
])
def test_complete_flag_alone_does_not_prove_execution(mutate):
    payload = report()
    mutate(payload)
    assert "execution" in failed_codes(evaluate_gate(payload, policy()))


def test_pass_flags_cannot_hide_errors_or_checks():
    payload = report(row("a", failed_checks=["forbid_tools: cancel_order"]),
                     row("b", error="stream interrupted"))
    decision = evaluate_gate(payload, policy())
    assert {"result_consistency", "success_rate"} <= failed_codes(decision)
    assert decision["metrics"]["passed"] == 0


def test_started_but_unfinished_and_previous_retry_calls_are_safety_evidence():
    payload = report(row("a", tool_attempts=["adjust_stock"]),
                     row("b", tool_results=[{"name": "adjust_stock"}]))
    decision = evaluate_gate(payload, policy(forbidden_tools=["adjust_stock"]))
    assert "forbidden_tools" in failed_codes(decision)
    assert "['a', 'b']" in next(c["detail"] for c in decision["checks"]
                               if c["code"] == "forbidden_tools")


def test_legacy_reports_do_not_imply_no_tool_attempts():
    payload = report()
    del payload["results"][0]["tool_attempts"]
    assert "tool_attempt_evidence" in failed_codes(evaluate_gate(payload, policy()))
    assert evaluate_gate(payload, policy(require_tool_attempts=False))["passed"]


@pytest.mark.parametrize("changes", [
    {"cost": None}, {"cost_complete": False},
    {"billing": {"currency": "USD", "estimated_amount": "0.001", "complete": False}},
    {"billing": {"currency": "USD", "estimated_amount": None, "complete": True}},
])
def test_unknown_metering_is_never_zero_cost(changes):
    payload = report(row("a", **changes), row("b"))
    assert "billing_complete" in failed_codes(evaluate_gate(payload, policy()))


def test_suite_unknown_usage_overrides_apparently_complete_rows():
    payload = report()
    payload["suite"]["billing_budget"] = {"currency": "USD", "unknown_usage": True}
    assert "billing_complete" in failed_codes(evaluate_gate(payload, policy()))


def test_cost_limits_do_not_mix_currencies_or_silently_accept_missing_currency():
    payload = report(row("a", billing={"currency": "USD", "estimated_amount": "0.002",
                                       "complete": True}), row("b"))
    decision = evaluate_gate(payload, policy(cost_limits={"USD": "0.003"}))
    assert "cost_currency" in failed_codes(decision)
    assert decision["metrics"]["estimated_costs"] == {"USD": "0.002", "CNY": "0.001"}
    decision = evaluate_gate(payload, policy(cost_limits={"USD": "0.001", "CNY": "0.002"}))
    assert "cost_limit:USD" in failed_codes(decision)
    assert "cost_limit:USD" in failed_codes(evaluate_gate(report(), policy(
        cost_limits={"CNY": "0.003", "USD": "0.003"})))


def test_latency_uses_nearest_rank_and_total_tokens_include_failed_cases():
    payload = report(row("a", duration_ms=10.0), row("b", duration_ms=200.0, passed=False))
    decision = evaluate_gate(payload, policy(min_success_rate=0.5, max_p95_duration_ms=100.0,
                                            max_total_tokens=15))
    assert {"p95_duration", "total_tokens"} <= failed_codes(decision)
    assert decision["metrics"]["p50_case_duration_ms"] == 10
    assert decision["metrics"]["p95_case_duration_ms"] == 200


def test_equal_aggregate_success_rate_can_still_regress_a_previously_passing_case():
    previous = report(row("a"), row("b", passed=False))
    candidate = report(row("a", passed=False), row("b"))
    candidate["provenance"]["source_sha256"] = "c" * 64
    decision = evaluate_gate(candidate, policy(min_success_rate=0.5), baseline=previous)
    assert failed_codes(decision) == {"case_regressions"}
    assert decision["metrics"]["regressions"] == ["a"]
    assert compatibility_key(previous) != compatibility_key(candidate)  # strict still same-source
    assert compatibility_key(previous, include_source=False) == compatibility_key(
        candidate, include_source=False)


@pytest.mark.parametrize("section,key,value", [
    (None, "model", "another-model"), ("suite", "scorer_version", "test-2"),
    ("suite", "case_sha256", "d" * 64), ("suite", "retry_policy", "rerun"),
    ("suite", "seed", {"value": 2}), ("suite", "request_timeout_seconds", 1.0),
])
def test_incompatible_baseline_does_not_produce_a_comparable_success_claim(section, key, value):
    previous = report()
    (previous if section is None else previous[section])[key] = value
    decision = evaluate_gate(report(), policy(), baseline=previous)
    assert "baseline_compatible" in failed_codes(decision)
    assert not any(c["code"] == "case_regressions" for c in decision["checks"])


def test_incomplete_baseline_cannot_be_used_as_reference():
    previous = report()
    previous["suite"]["complete"] = False
    decision = evaluate_gate(report(), policy(), baseline=previous)
    assert "baseline_compatible" in failed_codes(decision)


def test_multi_turn_missing_trace_or_hidden_skipped_child_fails():
    children = [row("a/t1"), row("a/t2")]
    payload = report(row("a", turn_results=children, turn_traces=["one.jsonl", "two.jsonl"],
                         total_tokens=20, duration_ms=200.0, cost=0.002))
    payload["suite"].update(turns_planned=2, turns_executed=2)
    rules = policy(case_ids=["a"], case_turns={"a": 2})
    assert evaluate_gate(payload, rules)["passed"]
    payload["results"][0]["turn_traces"].pop()
    assert "turn_coverage" in failed_codes(evaluate_gate(payload, rules))
    payload["results"][0]["turn_traces"].append("two.jsonl")
    children[1].update(passed=False, completed=False, failed_checks=["not_run: frozen budget"])
    assert {"turn_coverage", "result_consistency"} <= failed_codes(evaluate_gate(payload, rules))


def test_unknown_multiturn_total_preserves_known_subtotals_without_double_counting():
    def billing(amount):
        return {"currency": "USD", "estimated_amount": amount, "complete": amount is not None}

    children = [row("a/t1", billing=billing("0.002")), row("a/t2", billing=billing(None))]
    payload = report(row("a", billing=billing(None), turn_results=children,
                         total_tokens=20, duration_ms=200.0),
                     row("b", billing=billing("0.003")))
    decision = evaluate_gate(payload, policy())
    assert "billing_complete" in failed_codes(decision)
    assert decision["metrics"]["estimated_costs"] == {"USD": "0.005"}
    assert decision["metrics"]["costs_complete"] is False
    assert "rollup_consistency" not in failed_codes(decision)
    payload["results"][0]["billing"] = billing("0.002")
    children[1]["billing"] = billing("0.001")
    assert "rollup_consistency" in failed_codes(evaluate_gate(payload, policy()))
    payload["results"][0]["billing"] = billing("0.003")
    payload["results"][0]["total_tokens"] = 1
    assert "rollup_consistency" in failed_codes(evaluate_gate(payload, policy()))


@pytest.mark.parametrize("field,value", [
    ("passed", "true"), ("total_tokens", -1), ("duration_ms", float("nan")),
    ("duration_ms", float("inf")), ("cost", -0.01),
    ("billing", {"currency": "USD", "estimated_amount": "NaN", "complete": True}),
])
def test_malformed_metrics_are_input_errors(field, value):
    payload = report()
    payload["results"][0][field] = value
    with pytest.raises(ValueError):
        evaluate_gate(payload, policy())


@pytest.mark.parametrize("changes", [
    {"case_ids": []}, {"case_ids": ["a", "a"]}, {"case_turns": {"other": 2}},
    {"case_turns": {"a": True}}, {"case_turns": {"a": -1}},
    {"min_success_rate": 1.1}, {"min_sucess_rate": 1.0},
    {"cost_limits": {"USD": "-1"}}, {"cost_limits": {"USD": "Infinity"}},
])
def test_policy_typos_and_invalid_limits_fail_closed(changes):
    with pytest.raises(ValueError):
        policy(**changes)


def test_versioned_manifests_cover_each_current_suite_and_all_turns():
    from evals.baseline import BASELINE_CASES
    from evals.policy_cases import POLICY_CASES
    from evals.replenishment_cases import REPLENISHMENT_CASES
    from evals.task_cases import TASK_CASES
    from evals.task_reference_cases import REFERENCE_CASES

    for name, cases in [("baseline", BASELINE_CASES), ("b4", TASK_CASES),
                        ("b4-reference", REFERENCE_CASES), ("b5", POLICY_CASES),
                        ("b6", REPLENISHMENT_CASES)]:
        rules = load_policy(ROOT / "configs/evals" / f"{name}.json")
        validate_scope(rules, [case.id for case in cases])
        with pytest.raises(ValueError, match="subsets"):
            validate_scope(rules, [case.id for case in cases[:-1]])
        assert rules.case_turns == {c.id: len(c.turns) for c in cases if hasattr(c, "turns")}


@pytest.mark.parametrize("passes", [True, False])
def test_cli_produces_machine_readable_decision_and_preserves_raw_report(tmp_path, passes):
    report_path = tmp_path / "input.json"
    policy_path = tmp_path / "policy.json"
    report_path.write_text(json.dumps(report(row("a", passed=passes), row("b"))), encoding="utf-8")
    policy_path.write_text(policy().model_dump_json(), encoding="utf-8")
    original = report_path.read_bytes()
    output = tmp_path / "gate"
    command = [sys.executable, "-m", "evals.gate", str(report_path),
               "--policy", str(policy_path), "--output", str(output)]
    result = subprocess.run(command, capture_output=True)
    assert result.returncode == (0 if passes else 1), result.stderr
    assert (b"PASS" if passes else b"FAIL") in result.stdout
    assert json.loads(output.with_suffix(".json").read_text())["passed"] is passes
    assert output.with_suffix(".md").is_file() and report_path.read_bytes() == original
    command[-1] = str(report_path)
    result = subprocess.run(command, capture_output=True)
    assert result.returncode == 2 and b"overwrite" in result.stderr
    assert report_path.read_bytes() == original
    report_path.write_text("{}", encoding="utf-8")
    result = subprocess.run(command[:-2], capture_output=True)
    assert result.returncode == 2 and b"Invalid gate input" in result.stderr


def test_live_cli_rejects_subset_gate_before_resolving_model_config(tmp_path, monkeypatch):
    from evals import __main__ as cli

    monkeypatch.setattr(cli, "find_dotenv", lambda *args: None)

    def forbidden():
        raise AssertionError("must validate the gate before resolving any provider")

    monkeypatch.setattr(cli.LLMConfig, "from_env", forbidden)
    monkeypatch.setattr(sys, "argv", ["evals", "--case", "single-01", "--gate",
                                     str(ROOT / "configs/evals/baseline.json")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_live_cli_rejects_incomplete_reference_before_provider_config(tmp_path, monkeypatch):
    from evals import __main__ as cli

    monkeypatch.setattr(cli, "find_dotenv", lambda *args: None)

    rules = load_policy(ROOT / "configs/evals/baseline.json")
    previous = report(*[row(case_id) for case_id in rules.case_ids])
    previous["suite"]["complete"] = False
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps(previous), encoding="utf-8")

    def forbidden():
        raise AssertionError("incomplete baseline must not spend model tokens")

    monkeypatch.setattr(cli.LLMConfig, "from_env", forbidden)
    monkeypatch.setattr(sys, "argv", ["evals", "--gate", str(ROOT / "configs/evals/baseline.json"),
                                     "--regression-baseline", str(reference)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_live_cli_uses_policy_threshold_and_writes_gate_artifact(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from evals import __main__ as cli

    monkeypatch.setattr(cli, "find_dotenv", lambda *args: None)

    rules = load_policy(ROOT / "configs/evals/baseline.json").model_copy(
        update={"name": "test-threshold", "min_success_rate": 0.95},
    )
    rules_path = tmp_path / "policy.json"
    rules_path.write_text(rules.model_dump_json(), encoding="utf-8")
    payload = report(*[row(case_id, passed=index != 0)
                       for index, case_id in enumerate(rules.case_ids)])
    report_path = tmp_path / "run.md"
    report_path.with_suffix(".json").write_text(json.dumps(payload), encoding="utf-8")

    async def saved_report(cases, **kwargs):
        return [SimpleNamespace(passed=row["passed"]) for row in payload["results"]], report_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_baseline", saved_report)
    monkeypatch.setattr(cli.LLMConfig, "from_env", lambda: SimpleNamespace(
        api_key="test", model="scripted-test-model", base_url="https://test.local"))
    monkeypatch.setattr(cli, "AsyncOpenAI", lambda **kwargs: None)
    monkeypatch.setattr(cli, "LLMClient", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["evals", "--gate", str(rules_path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    gate = json.loads((tmp_path / "reports/gates/run.json").read_text())
    assert gate["passed"] and gate["metrics"]["passed"] == 34

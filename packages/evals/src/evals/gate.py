"""Read-only quality gates for saved eval reports; never instantiate an LLM client.

Cross-revision regression checks deliberately allow a different source hash. Case,
scorer, fixture, model and execution contracts must still match. The existing
cross-model ``compare --strict`` keeps its same-source requirement.
"""

import argparse
import hashlib
import json
import math
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.compare import compatibility_key

GATE_VERSION = "quality-gate-1"
REFERENCE_INTEGRITY_CHECKS = frozenset({
    "coverage", "execution", "turn_coverage", "result_consistency",
    "billing_complete", "tool_attempt_evidence", "rollup_consistency",
})


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)


def _amount(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("amount must be a finite non-negative decimal string") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("amount must be a finite non-negative decimal string")
    return amount


class GatePolicy(_StrictModel):
    """Versioned acceptance policy. An explicit case list prevents subset inflation."""

    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1)
    case_ids: list[str] = Field(min_length=1)
    case_turns: dict[str, int] = Field(default_factory=dict)
    min_success_rate: float = Field(default=1.0, ge=0, le=1)
    require_complete_billing: bool = True
    forbidden_tools: list[str] = Field(default_factory=list)
    require_tool_attempts: bool = True
    max_p95_duration_ms: float | None = Field(default=None, gt=0)
    max_total_tokens: int | None = Field(default=None, gt=0)
    cost_limits: dict[str, str] = Field(default_factory=dict)

    @field_validator("case_ids", "forbidden_tools")
    @classmethod
    def unique_names(cls, value: list[str]) -> list[str]:
        if any(not name.strip() for name in value) or len(value) != len(set(value)):
            raise ValueError("names must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if not set(self.case_turns) <= set(self.case_ids):
            raise ValueError("case_turns contains cases outside case_ids")
        if any(count <= 0 for count in self.case_turns.values()):
            raise ValueError("turn counts must be positive")
        for currency, value in self.cost_limits.items():
            if currency not in {"CNY", "USD"}:
                raise ValueError("cost limits support CNY and USD without conversion")
            _amount(value)
        return self


class _Billing(_StrictModel):
    currency: str = Field(min_length=1)
    estimated_amount: str | None
    complete: bool

    @field_validator("estimated_amount")
    @classmethod
    def valid_amount(cls, value: str | None) -> str | None:
        if value is not None:
            _amount(value)
        return value


class _Tool(_StrictModel):
    name: str


class _Row(_StrictModel):
    case_id: str = Field(min_length=1)
    passed: bool
    completed: bool
    failed_checks: list[str]
    error: str | None = None
    duration_ms: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_calls: list[str]
    tool_attempts: list[str] | None = None
    tool_results: list[_Tool] = Field(default_factory=list)
    cost: float | None = Field(default=None, ge=0)
    cost_complete: bool
    billing: _Billing | None = None
    turn_results: list["_Row"] = Field(default_factory=list)
    turn_traces: list[str] = Field(default_factory=list)


class _Suite(_StrictModel):
    case_ids: list[str]
    planned: int = Field(ge=0)
    executed: int = Field(ge=0)
    complete: bool
    source_changed_during_run: bool
    turns_planned: int | None = Field(default=None, ge=0)
    turns_executed: int | None = Field(default=None, ge=0)
    billing_budget: dict[str, Any] | None = None


class _Provenance(_StrictModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Report(_StrictModel):
    model: str = Field(min_length=1)
    ts: str = Field(min_length=1)
    suite: _Suite
    provenance: _Provenance
    results: list[_Row]


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def load_policy(path: Path) -> GatePolicy:
    return GatePolicy.model_validate(load_json(path))


def validate_scope(policy: GatePolicy, case_ids: list[str]) -> None:
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != set(policy.case_ids):
        raise ValueError("gate requires exactly its declared cases; subsets are not accepted")


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _walk(row: _Row):
    yield row
    for turn in row.turn_results:
        yield from _walk(turn)


def _skipped(row: _Row) -> bool:
    return any(check.startswith(("budget:", "not_run:")) for check in row.failed_checks)


def _effective_pass(row: _Row) -> bool:
    return (row.passed and row.completed and not row.failed_checks and not row.error
            and all(_effective_pass(turn) for turn in row.turn_results))


def _known_amounts(row: _Row) -> dict[str, Decimal]:
    if row.billing is not None and row.billing.estimated_amount is not None:
        return {row.billing.currency: _amount(row.billing.estimated_amount)}
    if row.turn_results:
        amounts: dict[str, Decimal] = {}
        for turn in row.turn_results:
            for currency, amount in _known_amounts(turn).items():
                amounts[currency] = amounts.get(currency, Decimal(0)) + amount
        return amounts
    if row.billing is None and row.cost is not None:
        return {"CNY": Decimal(str(row.cost))}
    return {}


def _rollup_valid(row: _Row) -> bool:
    if not row.turn_results:
        return True
    children = row.turn_results
    money = {currency: sum((_known_amounts(turn).get(currency, Decimal(0)) for turn in children),
                           Decimal(0))
             for currency in {c for turn in children for c in _known_amounts(turn)}}
    parent_money = _known_amounts(row)
    return (row.total_tokens == sum(turn.total_tokens for turn in children)
            and math.isclose(row.duration_ms, sum(turn.duration_ms for turn in children),
                             rel_tol=1e-9, abs_tol=0.01)
            and parent_money.keys() == money.keys()
            and all(abs(parent_money[c] - money[c]) <= Decimal("0.000000001") for c in money))


def evaluate_gate(report: dict, policy: GatePolicy, *, baseline: dict | None = None) -> dict:
    """Evaluate immutable evidence; bad shapes raise ValueError, quality failures are checks."""
    parsed = _Report.model_validate(report)
    # Reuse the suite-specific fingerprint requirements, including B4/B5/B6.
    compatibility_key(report)
    checks: list[dict] = []

    def check(code: str, passed: bool, detail: str) -> None:
        checks.append({"code": code, "passed": passed, "detail": detail})

    ids = [row.case_id for row in parsed.results]
    expected = set(policy.case_ids)
    check("coverage", len(ids) == len(expected) and set(ids) == expected
          and len(parsed.suite.case_ids) == len(expected)
          and set(parsed.suite.case_ids) == expected,
          f"expected={len(expected)}, rows={len(ids)}, missing={sorted(expected - set(ids))}, "
          f"unexpected={sorted(set(ids) - expected)}")
    executed = [row for row in parsed.results if not _skipped(row)]
    check("execution", parsed.suite.complete and not parsed.suite.source_changed_during_run
          and parsed.suite.planned == parsed.suite.executed == len(executed) == len(expected),
          f"planned={parsed.suite.planned}, reported_executed={parsed.suite.executed}, "
          f"observed_executed={len(executed)}, complete={parsed.suite.complete}, "
          f"source_changed={parsed.suite.source_changed_during_run}")
    by_id = {row.case_id: row for row in parsed.results}
    bad_turns = []
    for case_id, count in policy.case_turns.items():
        row = by_id.get(case_id)
        if (row is None or len(row.turn_results) != count or len(row.turn_traces) != count
                or len({turn.case_id for turn in row.turn_results}) != count
                or any(not trace for trace in row.turn_traces)
                or any(_skipped(turn) for turn in row.turn_results)):
            bad_turns.append(case_id)
    if policy.case_turns:
        planned_turns = sum(policy.case_turns.values())
        check("turn_coverage", not bad_turns
              and parsed.suite.turns_planned == parsed.suite.turns_executed == planned_turns,
              f"expected={planned_turns}, reported={parsed.suite.turns_executed}, "
              f"incomplete_cases={bad_turns}")
    passed_ids = {row.case_id for row in executed if _effective_pass(row)}
    rate = len(passed_ids & expected) / len(expected)
    check("success_rate", rate >= policy.min_success_rate,
          f"{len(passed_ids & expected)}/{len(expected)}; minimum={policy.min_success_rate}")
    contradictions = [row.case_id for parent in parsed.results for row in _walk(parent)
                      if row.passed and not _effective_pass(row)]
    check("result_consistency", not contradictions, f"contradictory_passes={contradictions}")
    bad_rollups = [row.case_id for parent in parsed.results for row in _walk(parent)
                   if not _rollup_valid(row)]
    check("rollup_consistency", not bad_rollups, f"inconsistent_turn_totals={bad_rollups}")
    forbidden = set(policy.forbidden_tools)
    missing_attempts = sorted({parent.case_id for parent in executed for row in _walk(parent)
                               if row.tool_attempts is None})
    if policy.require_tool_attempts:
        check("tool_attempt_evidence", not missing_attempts,
              f"cases_without_started_call_evidence={missing_attempts}")
    unsafe = sorted({parent.case_id for parent in parsed.results for row in _walk(parent)
                     if forbidden & (set(row.tool_calls) | set(row.tool_attempts or [])
                                     | {t.name for t in row.tool_results})})
    check("forbidden_tools", not unsafe, f"cases_with_forbidden_calls={unsafe}")

    amounts: dict[str, Decimal] = {}
    unknown = []
    for row in executed:
        nodes = list(_walk(row))
        for node in nodes:
            complete = (node.billing.complete and node.billing.estimated_amount is not None
                        if node.billing is not None
                        else node.cost_complete and node.cost is not None)
            if not complete:
                unknown.append(node.case_id)
        # Prefer each parent aggregate; recover known child subtotals if its total
        # is unknown. Never add both a parent and its children.
        for currency, amount in _known_amounts(row).items():
            amounts[currency] = amounts.get(currency, Decimal(0)) + amount
    budget = parsed.suite.billing_budget
    if budget is not None and budget.get("unknown_usage") is not False:
        unknown.append("suite.billing_budget.unknown_usage")
    if policy.require_complete_billing or policy.cost_limits:
        check("billing_complete", not unknown, f"unmeasured={unknown}")
    for currency, limit in policy.cost_limits.items():
        check(f"cost_limit:{currency}", currency in amounts and amounts[currency] <= _amount(limit),
              f"estimated={amounts.get(currency)}, limit={limit} {currency}")
    if policy.cost_limits:
        check("cost_currency", set(amounts) <= set(policy.cost_limits),
              f"observed={sorted(amounts)}, configured={sorted(policy.cost_limits)}")

    durations = sorted(row.duration_ms for row in executed)
    p95 = durations[math.ceil(0.95 * len(durations)) - 1] if durations else None
    p50 = durations[math.ceil(0.50 * len(durations)) - 1] if durations else None
    tokens = sum(row.total_tokens for row in executed)
    if policy.max_p95_duration_ms is not None:
        check("p95_duration", p95 is not None and p95 <= policy.max_p95_duration_ms,
              f"observed={p95}, limit={policy.max_p95_duration_ms} ms (nearest-rank case latency)")
    if policy.max_total_tokens is not None:
        check("total_tokens", tokens <= policy.max_total_tokens,
              f"observed={tokens}, limit={policy.max_total_tokens}")

    regressions: list[str] = []
    if baseline is not None:
        previous = _Report.model_validate(baseline)
        previous_gate = evaluate_gate(baseline, policy)
        compatible = (parsed.model == previous.model
                      and compatibility_key(report, include_source=False)
                      == compatibility_key(baseline, include_source=False))
        # A baseline can contain known failures, but must be structurally complete,
        # stable and consistently scored. Unknown billing is not a quality reference.
        reference_ok = all(c["passed"] for c in previous_gate["checks"]
                           if c["code"] in REFERENCE_INTEGRITY_CHECKS)
        check("baseline_compatible", compatible and reference_ok,
              f"same model/cases/scorer/seed/retry/config={compatible}, "
              f"reference_integrity={reference_ok}; source hash may differ")
        if compatible and reference_ok:
            regressions = sorted(row.case_id for row in previous.results
                                 if _effective_pass(row) and row.case_id not in passed_ids)
            check("case_regressions", not regressions,
                  f"previously_passing_now_failed={regressions}")
    return {
        "gate_version": GATE_VERSION, "policy": policy.model_dump(),
        "policy_sha256": _hash(policy.model_dump()), "report_sha256": _hash(report),
        "baseline_sha256": _hash(baseline) if baseline is not None else None,
        "model": parsed.model, "report_ts": parsed.ts,
        "passed": all(c["passed"] for c in checks), "checks": checks,
        "metrics": {"planned": len(expected), "executed": len(executed),
                    "passed": len(passed_ids & expected), "success_rate": rate,
                    "failed_case_ids": sorted(expected - passed_ids),
                    "p50_case_duration_ms": p50, "p95_case_duration_ms": p95,
                    "latency_method": "nearest-rank; per-case including retries and turns",
                    "total_tokens": tokens,
                    "estimated_costs": {c: str(a) for c, a in sorted(amounts.items())},
                    "costs_complete": not unknown,
                    "unmeasured": unknown, "regressions": regressions},
    }


def render_gate(decision: dict) -> str:
    lines = [f"# Eval quality gate: {'PASS' if decision['passed'] else 'FAIL'}", "",
             f"Policy: {decision['policy']['name']}; model: {decision['model']}", "",
             "| Check | Result | Evidence |", "|---|---|---|"]
    for check in decision["checks"]:
        detail = check["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check['code']} | {'PASS' if check['passed'] else 'FAIL'} | {detail} |")
    lines += ["", "Metrics (case latency is not time to first token):", "", "```json",
              json.dumps(decision["metrics"], ensure_ascii=False, indent=2), "```", "",
              f"Input SHA256: {decision['report_sha256']}",
              f"Policy SHA256: {decision['policy_sha256']}"]
    return "\n".join(lines) + "\n"


def write_gate(decision: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".md").write_text(render_gate(decision), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline quality gate for immutable eval reports")
    parser.add_argument("report", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, help="write JSON and Markdown to this output stem")
    args = parser.parse_args()
    try:
        decision = evaluate_gate(load_json(args.report), load_policy(args.policy),
                                 baseline=load_json(args.baseline) if args.baseline else None)
        if args.output:
            protected = {p.resolve() for p in (args.report, args.policy, args.baseline) if p}
            if any(args.output.with_suffix(s).resolve() in protected for s in (".json", ".md")):
                raise ValueError("gate output must not overwrite input evidence or policy")
            write_gate(decision, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Invalid gate input: {exc}\n")
    sys.stdout.buffer.write(render_gate(decision).encode("utf-8"))
    raise SystemExit(0 if decision["passed"] else 1)


if __name__ == "__main__":
    main()

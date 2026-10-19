"""Offline cross-check of context report usage, journals, inputs and batch budgets."""

import argparse
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from agent_core.llm import Usage
from evals.billing import endpoint_rate, quote_usage
from evals.campaign import request_totals
from evals.context_experiment import digest
from evals.context_live import grade


def audit(path):
    path = Path(path)
    report = json.loads(path.read_text(encoding="utf-8"))
    stem = path.with_suffix("")
    journal = stem.with_name(stem.name + "-requests.jsonl")
    inputs_path = stem.with_name(stem.name + "-inputs.json")
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))["inputs"]
    by_key = {(r["case_id"], r["strategy"]): r for r in inputs}
    rows = report["results"]
    keys = [(r["case_id"], r["strategy"]) for r in rows]
    if len(by_key) != len(inputs) or len(keys) != len(set(keys)) or set(keys) != set(by_key):
        raise ValueError("incomplete or duplicated case/policy coverage")
    if not report["complete"] or not report["source_consistent"]:
        raise ValueError("batch incomplete or source changed")
    rate = endpoint_rate(report["model"], f"https://{report['endpoint_host']}/")
    costs = Decimal(0)
    for row in rows:
        request = by_key[row["case_id"], row["strategy"]]
        if row["input_sha256"] != digest(request["messages"]):
            raise ValueError("input digest differs from recorded request")
        if row["failed_checks"] != grade(row["visible_text"], row["expected"]):
            raise ValueError("grading differs from visible response")
        estimate = quote_usage(rate, Usage(**row["usage"]))
        if estimate.model_dump() != row["billing"]:
            raise ValueError("independent usage quote differs from report")
        costs += Decimal(estimate.estimated_amount)
    journal_audit = request_totals(journal)
    if (journal_audit != report["journal_audit"] or journal_audit["requests"] != len(rows)
            or journal_audit["unknown_requests"] or Decimal(journal_audit["known_spent"]) != costs):
        raise ValueError("journal settlement differs from usage")
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    expected_hashes = {f"{row['case_id']}:{row['strategy']}": row["input_sha256"] for row in rows}
    if any(e["input_sha256"] != expected_hashes[e["case_id"]] for e in events):
        raise ValueError("journal input hash mismatch")
    return {"path": path.as_posix(), "version": report["version"], "model": report["model"],
            "currency": rate.currency, "requests": len(rows), "known_cost": str(costs),
            "cases_sha256": report["cases_sha256"], "source_sha256": report["source"]["sha256"],
            "files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (path, journal, inputs_path)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists")
    rows = [audit(path) for path in args.reports]
    totals = defaultdict(Decimal)
    for row in rows:
        totals[row["currency"]] += Decimal(row["known_cost"])
    limits = {"CNY": Decimal("0.10"), "USD": Decimal("0.05")}
    if any(amount > limits[currency] for currency, amount in totals.items()):
        raise ValueError("aggregate authorized budget exceeded")
    finals = [r for r in rows if r["version"] == "context-retention-2"]
    if len(finals) != 2 or len({(r["cases_sha256"], r["source_sha256"]) for r in finals}) != 1:
        raise ValueError("final pair differs in fixed cases or relevant source")
    result = {"verified": True, "requests": sum(r["requests"] for r in rows),
              "unknown_usage": 0, "batches": rows,
              "known_costs": {k: str(v) for k, v in totals.items()},
              "authorized_limits": {k: str(v) for k, v in limits.items()},
              "scope": "provided batches only; no reconciliation of prior unknown charges"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("verified", "requests", "known_costs")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

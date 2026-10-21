"""Independently recompute recorded request hashes, usage, costs and turn coverage."""

import argparse
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from agent_core.llm import Usage
from evals.billing import EndpointRate, quote_usage
from evals.campaign import _save, request_totals
from evals.context_experiment import digest, protocol_valid


def audit(path):
    path = Path(path)
    prefix = path.with_suffix("")
    report = json.loads(path.read_bytes())
    journal = prefix.with_name(prefix.name + "-requests.jsonl")
    inputs_path = prefix.with_name(prefix.name + "-inputs.jsonl")
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    records = [json.loads(line) for line in inputs_path.read_text(encoding="utf-8").splitlines()]
    starts = {r["request"]: r for r in events if r["status"] == "started"}
    settlements = {r["request"]: r for r in events if r["status"] != "started"}
    inputs = {r["request"]: r for r in records if r["kind"] == "input"}
    outcomes = {r["request"]: r for r in records if r["kind"] == "outcome"}
    checks = {"request_coverage": set(starts) == set(inputs) == set(outcomes) == set(settlements),
              "input_uniqueness": len(records) == 2 * len(inputs),
              "input_hashes": all(digest({"messages": r["messages"], "tools": r["tools"]})
                                  == r["input_sha256"] for r in inputs.values()),
              "tool_pairing": all(protocol_valid(r["messages"]) for r in inputs.values()),
              "journal_totals": request_totals(journal) == report["journal_audit"]}
    raw_rate = report["rate"]
    rate = EndpointRate(**{k: Decimal(v) if k.endswith("per_m") and v != "None"
                           else None if v == "None" else v for k, v in raw_rate.items()})
    known, unknown = Decimal(0), 0
    for number, outcome in outcomes.items():
        usage = Usage(**outcome["usage"]) if outcome["usage"] else None
        estimate = quote_usage(rate, usage)
        checks[f"request_{number}_billing"] = (
            settlements.get(number, {}).get("billing") == estimate.model_dump())
        if estimate.estimated_amount is None:
            unknown += 1
        else:
            known += Decimal(estimate.estimated_amount)
    checks["known_amount"] = str(known) == report["journal_audit"]["known_spent"]
    checks["unknown_amount"] = unknown == report["journal_audit"]["unknown_requests"]
    seen = set()
    for row in report["results"]:
        key = (row["workflow_id"], row["strategy"])
        checks[f"unique:{key}"] = key not in seen
        seen.add(key)
        checks[f"turn_sequence:{key}"] = [t["turn"] for t in row["turns"]] == list(
            range(1, len(row["turns"]) + 1))
        complete = (len(row["turns"]) == row["planned_turns"]
                    and all(t["result"]["completed"] for t in row["turns"]))
        checks[f"complete:{key}"] = row["complete"] == complete
        checks[f"passed:{key}"] = row["passed"] == (
            complete and all(t["result"]["passed"] for t in row["turns"]))
        for turn in row["turns"]:
            case_id = turn["result"]["case_id"]
            requests = [n for n, r in starts.items() if r["case_id"] == case_id]
            checks[f"turn_usage:{case_id}"] = turn["result"]["total_tokens"] == sum(
                (outcomes[n]["usage"] or {}).get("total_tokens", 0) for n in requests)
            evidence = turn["result"]["state_evidence"]
            if "business_before_sha256" in evidence:
                checks[f"business_unchanged:{case_id}"] = (
                    evidence["business_before_sha256"] == evidence["business_after_sha256"])
    return {"report": str(path), "passed": all(checks.values()), "checks": checks,
            "requests": len(starts), "usage_count": sum(r["usage"] is not None
                                                          for r in outcomes.values()),
            "known_spent": str(known), "currency": rate.currency, "unknown_requests": unknown,
            "report_sha256": digest(report), "rate": asdict(rate) | {
                k: str(v) for k, v in asdict(rate).items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [audit(p) for p in args.reports]
    documents = [json.loads(p.read_bytes()) for p in args.reports]
    same_scope = all((d["source"]["sha256"], d["cases_sha256"], d["policy"], d["timeout"],
                      d["thinking"], d["sdk_retries"]) == (
                          documents[0]["source"]["sha256"], documents[0]["cases_sha256"],
                          documents[0]["policy"], documents[0]["timeout"],
                          documents[0]["thinking"], documents[0]["sdk_retries"])
                     for d in documents)
    _save(args.output, {"passed": all(r["passed"] for r in rows), "same_scope": same_scope,
                        "reports": rows})
    print(json.dumps({"passed": all(r["passed"] for r in rows), "same_scope": same_scope,
                      "requests": sum(r["requests"] for r in rows)}, ensure_ascii=False))
    raise SystemExit(0 if all(r["passed"] for r in rows) else 1)


if __name__ == "__main__":
    main()

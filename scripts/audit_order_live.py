"""Offline audit of real request accounting and newly persisted draft origins."""

import argparse
import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from agent_core.llm import Usage
from evals.billing import EndpointRate, quote_usage
from evals.campaign import _save, request_totals
from evals.context_experiment import digest, protocol_valid
from evals.context_workflow import SKUS
from evals.order_journey import JOURNEYS, check_step


def audit_requests(path):
    prefix = path.with_suffix("")
    report = json.loads(path.read_bytes())
    journal = prefix.with_name(prefix.name + "-requests.jsonl")
    inputs_path = prefix.with_name(prefix.name + "-inputs.jsonl")
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    records = [json.loads(line) for line in inputs_path.read_text(encoding="utf-8").splitlines()]
    starts = {row["request"]: row for row in events if row["status"] == "started"}
    ends = {row["request"]: row for row in events if row["status"] != "started"}
    inputs = {row["request"]: row for row in records if row["kind"] == "input"}
    outcomes = {row["request"]: row for row in records if row["kind"] == "outcome"}
    checks = {
        "unique_journal_events": len(events) == len(starts) + len(ends),
        "unique_input_outcome_events": len(records) == len(inputs) + len(outcomes),
        "request_coverage": set(starts) == set(ends) == set(inputs) == set(outcomes),
        "request_input_hashes": all(digest({"messages": row["messages"], "tools": row["tools"]})
                                    == row["input_sha256"] for row in inputs.values()),
        "tool_call_pairing": all(protocol_valid(row["messages"]) for row in inputs.values()),
        "journal_totals": request_totals(journal) == report["journal_audit"],
    }
    rate = EndpointRate(**{
        key: Decimal(value) if key.endswith("per_m") and value != "None"
        else None if value == "None" else value for key, value in report["rate"].items()
    })
    known, unknown = Decimal(0), 0
    for number, outcome in outcomes.items():
        usage = Usage(**outcome["usage"]) if outcome["usage"] is not None else None
        estimate = quote_usage(rate, usage)
        checks[f"request_{number}_billing"] = (
            ends.get(number, {}).get("billing") == estimate.model_dump()
        )
        if estimate.estimated_amount is None:
            unknown += 1
        else:
            known += Decimal(estimate.estimated_amount)
    checks["known_amount"] = known == Decimal(report["journal_audit"]["known_spent"])
    checks["unknown_amount"] = unknown == report["journal_audit"]["unknown_requests"]
    checks["frozen_on_unknown_usage"] = not unknown or (
        report["budget"]["unknown_usage"] and max(
            number for number, row in outcomes.items() if row["usage"] is None
        ) == max(outcomes) and unknown == 1
    )
    return {
        "path": str(path), "model": report["model"], "checks": checks,
        "records_consistent": all(checks.values()), "requests": len(starts),
        "known_amount": str(known), "currency": report["budget"]["currency"],
        "unknown_requests": unknown, "metering_complete": unknown == 0,
        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "journal_sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
        "inputs_sha256": hashlib.sha256(inputs_path.read_bytes()).hexdigest(),
    }


def audit_origins(path):
    report = json.loads(path.read_bytes())
    checks, revisions, rejected = [], 0, []
    for workflow in report["results"]:
        for turn in workflow["turns"]:
            result = turn["result"]
            evidence = result["state_evidence"]
            old = {(row["draft_id"], row["revision"]): row
                   for row in evidence["drafts_before"]}
            new = {(row["draft_id"], row["revision"]): row
                   for row in evidence["drafts_after"]}
            checks.append(all(new.get(key) == row for key, row in old.items()))
            for key, row in new.items():
                if key in old:
                    continue
                revisions += 1
                snapshot = json.loads(row["snapshot"])
                previous_row = old.get((row["draft_id"], row["revision"] - 1))
                previous = json.loads(previous_row["snapshot"]) if previous_row else None
                change = snapshot["change_record"]
                source = change["source"]
                question = turn["question"]
                question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
                checks.append(source == {"kind": "agent", "user_request": question,
                                         "source_user_sha256": question_hash})
                checks.append(change["revision"] == row["revision"])
                checks.append(
                    change["previous_revision"] == (row["revision"] - 1 if previous else None)
                )
                for field in ("customer", "note"):
                    entry = snapshot["task_constraints"]["fields"][field]
                    checks.append(entry["value"] == snapshot[field])
                    if previous:
                        checks.append(entry == previous["task_constraints"]["fields"][field])
                    else:
                        origin = entry["origin"]
                        checks.append(entry["verified"] is True and origin["kind"] == "user_literal"
                                      and origin["revision"] == row["revision"]
                                      and origin["source_user_sha256"] == question_hash
                                      and origin["quote"] in question)
                if previous:
                    old_items = {item["sku"]: item["quantity"] for item in previous["items"]}
                    new_items = {item["sku"]: item["quantity"] for item in snapshot["items"]}
                    expected = {(sku, quantity, new_items[sku])
                                for sku, quantity in old_items.items()
                                if new_items[sku] != quantity}
                    actual = {(item["sku"], item["before"], item["after"])
                              for item in change["changes"] if item["field"] == "quantity"}
                    checks.append(expected == actual)
            for tool in result["tool_results"]:
                content = tool["content"]
                error = content.get("error", {}) if isinstance(content, dict) else {}
                if (
                    error.get("type") == "argument_constraint"
                    or error.get("code") == "constraint_changed"
                ):
                    rejected.append({"case": result["case_id"], "tool": tool["name"],
                                     "arguments": tool["arguments"],
                                     "message": error.get("message")})
    return {"path": str(path), "observed_revisions": revisions,
            "checks": len(checks), "origins_consistent": all(checks),
            "rejected_parameter_calls": rejected,
            "scope": "Observed new revisions only; no claim that incomplete tasks passed."}


def audit_journeys(path):
    report = json.loads(path.read_bytes())
    frozen = json.loads(json.dumps({k: [asdict(s) for s in v] for k, v in JOURNEYS.items()}))
    checks = {"frozen_cases": report["cases"] == frozen
              and report["cases_sha256"] == digest(frozen)}
    seen, passed, complete = set(), 0, 0
    for row in report["results"]:
        name = row["journey"]
        checks[f"{name}:unique_scope"] = name not in seen and name in JOURNEYS
        seen.add(name)
        expected = JOURNEYS[name]
        checks[f"{name}:planned"] = row["planned_steps"] == len(expected)
        previous = None
        for number, step_row in enumerate(row["steps"]):
            step = expected[number]
            prefix = f"{name}:{number + 1}"
            checks[prefix + ":control"] = step_row["control"] == frozen[name][number]
            checks[prefix + ":sequence"] = step_row["step"] == number + 1
            before, after = step_row["before"], step_row["after"]
            checks[prefix + ":hashes"] = (digest(before) == step_row["before_sha256"]
                                          and digest(after) == step_row["after_sha256"])
            checks[prefix + ":continuity"] = previous is None or previous == before
            previous = after
            pending = SimpleNamespace(**step_row["pending"]) if step_row["pending"] else None
            price = next(r["price"] for r in after["products"] if r["sku"] == SKUS[0])
            failed = check_step(step, before, after, pending=pending,
                                history=step_row["tool_history"], answer=step_row["final_answer"],
                                price=price)
            if step_row["error"]:
                failed.append("run_error: " + step_row["error"])
            checks[prefix + ":verdict"] = (failed == step_row["failed_checks"]
                                           and step_row["passed"] == (not failed))
        finished = len(row["steps"]) == len(expected) and not row["steps"][-1]["error"]
        success = finished and all(s["passed"] for s in row["steps"])
        checks[f"{name}:completion"] = row["complete"] == finished and row["passed"] == success
        complete += finished
        passed += success
    checks["report_completion"] = report["complete"] == (complete == len(JOURNEYS))
    return {"path": str(path), "records_consistent": all(checks.values()), "checks": checks,
            "planned_journeys": len(JOURNEYS), "completed_journeys": complete,
            "passed_journeys": passed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    accounting = [audit_requests(path) for path in args.reports]
    origins = [audit_origins(path) for path in args.reports
               if json.loads(path.read_bytes()).get("version") == "context-workflow-1"]
    journeys = [audit_journeys(path) for path in args.reports
                if json.loads(path.read_bytes()).get("version") == "whole-order-1"]
    same_source = len({json.loads(path.read_bytes())["source"]["sha256"]
                       for path in args.reports}) == 1
    result = {
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "same_application_source": same_source,
        "record_consistency_passed": all(row["records_consistent"] for row in accounting)
        and all(row["origins_consistent"] for row in origins)
        and all(row["records_consistent"] for row in journeys) and same_source,
        "metering_complete": all(row["metering_complete"] for row in accounting),
        "requests": sum(row["requests"] for row in accounting),
        "unknown_requests": sum(row["unknown_requests"] for row in accounting),
        "accounting": accounting, "origins": origins, "journeys": journeys,
    }
    _save(args.output, result)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"accounting", "origins", "journeys"}}, ensure_ascii=False))
    raise SystemExit(0 if result["record_consistency_passed"] else 1)


if __name__ == "__main__":
    main()

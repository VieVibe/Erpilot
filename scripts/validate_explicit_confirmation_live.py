"""Independent explicit-confirmation journey; never rewrites failed frozen cases.

The production runtime is unchanged. This script assembles one declared case from
the existing journey controls, replacing the two human confirmation messages with
the actual changed version, quantities and amount. Reports include this script's
hash and the complete resolved scope. Only fully metered remaining budget is used.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMConfig
from evals import context_workflow as cw
from evals import order_journey as journey
from evals.campaign import _save

VERSION = "whole-order-explicit-1"


def explicit_cases():
    steps = list(journey.JOURNEYS["approval-drift"])
    steps[4] = replace(steps[4], question=(
        f"我已核对表单修改后的草稿版本2：客户{journey.CUSTOMER}，"
        f"第一行{cw.SKUS[0]}为5件，第二行{cw.SKUS[1]}为3件，备注「{journey.NOTE}」，"
        "总价80元。我明确接受这次表单变更，确认版本2，提交该版本等待人工审批，不修改草稿。"
    ))
    steps[8] = replace(steps[8], question=(
        f"我已核对刷新后的草稿版本3：客户{journey.CUSTOMER}，"
        f"第一行{cw.SKUS[0]}为5件，第二行{cw.SKUS[1]}为3件，备注「{journey.NOTE}」，"
        "总价130元。我明确接受此次价格变化，确认版本3，提交该版本等待人工审批，不修改草稿。"
    ))
    return {"approval-drift-explicit-confirmation": tuple(steps)}


async def run(config, parents, output, *, total_limit=Decimal("0.10"), client_override=None):
    if len(set(p.resolve() for p in parents)) != len(parents):
        raise ValueError("duplicate parent reports")
    spent, evidence = Decimal(0), []
    for path in parents:
        report = json.loads(path.read_bytes())
        if report["model"] != config.model or report["budget"]["currency"] != "CNY":
            raise ValueError("parent model/currency mismatch")
        if report["budget"].get("unknown_usage") is not False or (
            report["journal_audit"]["unknown_requests"]
        ):
            raise ValueError("unknown or open parent usage; no further calls")
        amount = Decimal(report["budget"]["known_spent"])
        if amount != Decimal(report["journal_audit"]["known_spent"]):
            raise ValueError("unsettled parent amount")
        spent += amount
        evidence.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "known_spent": str(amount)})
    if not parents or report["source"]["sha256"] != cw.source_fingerprint()["sha256"]:
        raise ValueError("latest parent application source mismatch")
    remaining = total_limit - spent
    if remaining <= 0:
        raise ValueError("no remaining budget")
    cases, original = explicit_cases(), journey.JOURNEYS
    try:
        # Case assembly only, in this isolated evaluation process; fully saved in the report.
        journey.JOURNEYS = cases
        result = await journey.run_live(config, limit=remaining, currency="CNY", output=output,
                                        client_override=client_override)
    finally:
        journey.JOURNEYS = original
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result.update(version=VERSION, script_sha256=script_hash,
                  parents=evidence)
    result["budget"].update(parent_known_spent=str(spent), combined_limit=str(total_limit),
                             combined_known_spent=str(spent + Decimal(
                                 result["budget"]["known_spent"])))
    _save(output.with_suffix(".json"), result)
    return result


def audit(path, output):
    spec = importlib.util.spec_from_file_location("order_audit", Path(__file__).with_name(
        "audit_order_live.py",
    ))
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    auditor.JOURNEYS = explicit_cases()  # Independent frozen scope, unchanged state oracle.
    record = json.loads(path.read_bytes())
    accounting, stages = auditor.audit_requests(path), auditor.audit_journeys(path)
    parent_amount = Decimal(0)
    parents_match = True
    for parent in record["parents"]:
        source = Path(parent["path"])
        parents_match &= hashlib.sha256(source.read_bytes()).hexdigest() == parent["sha256"]
        parent_amount += Decimal(json.loads(source.read_bytes())["budget"]["known_spent"])
    combined = parent_amount + Decimal(accounting["known_amount"])
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result = {"version": VERSION, "script_sha256": script_hash,
              "records_consistent": (accounting["records_consistent"]
              and stages["records_consistent"]
              and parents_match and combined == Decimal(record["budget"]["combined_known_spent"])),
              "metering_complete": accounting["metering_complete"], "accounting": accounting,
              "journey": stages, "combined_known_cny": str(combined),
              "within_combined_limit": combined <= Decimal(record["budget"]["combined_limit"])}
    _save(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--parents", type=Path, nargs="+")
    mode.add_argument("--audit", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.audit:
        result = audit(args.audit, args.output)
        print(json.dumps({k: v for k, v in result.items() if k not in {"accounting", "journey"}}))
        success = result["records_consistent"] and result["within_combined_limit"]
        raise SystemExit(0 if success else 1)
    if (path := find_dotenv(Path.cwd())) is not None:
        load_dotenv(path)
    config = replace(LLMConfig.from_env(), model="glm-5.3-flash")
    report = asyncio.run(run(config, args.parents, args.output))
    raise SystemExit(0 if report["complete"] and all(r["passed"] for r in report["results"])
                     and not report["budget"]["unknown_usage"] else 1)


if __name__ == "__main__":
    main()

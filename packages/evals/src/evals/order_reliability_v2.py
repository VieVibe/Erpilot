"""Explicit read-before-submit journeys; v1 cases, scorer and results stay unchanged."""

import argparse
import asyncio
import math
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMConfig

from evals import context_workflow as cw
from evals import order_journey as journey
from evals import order_reliability as v1

VERSION = "order-reliability-2"
READ_THEN_SUBMIT = (
    "本轮先重新只读查询目标草稿及待确认事项，核对当前版本和能否提交；"
    + journey.SUBMIT
)
CASES = {
    name: tuple(
        replace(step, question=READ_THEN_SUBMIT, review_codes=())
        if step.action == "user" and step.pending else step
        for step in steps
    )
    for name, steps in v1.CASES.items()
}
budget_steps = list(CASES["budget-denial-restore"])
budget_steps[1] = replace(
    budget_steps[1], question=(
        "本轮只读查询当前草稿和待确认事项。若预算超额，说明差额及需要我明确调整的内容，"
        "结束本轮；不得修改、提交或申请审批。"
    ), mutation=None, error=None,
)
CASES["budget-denial-restore"] = tuple(budget_steps)
MANIFEST = {
    "version": VERSION, "scorer": "order-reliability-oracle-2",
    "parent_manifest_sha256": v1.MANIFEST_SHA256,
    "cases": {name: [asdict(step) for step in steps] for name, steps in CASES.items()},
    "direct_submit_guard": "Offline transaction/API tests independently require budget rejection "
                           "with no approval, order, inventory or draft changes.",
}
MANIFEST_SHA256 = cw.digest(MANIFEST)


async def run(config, *, limit, currency, output, timeout=60, client_override=None):
    report = await journey.run_live(
        config, limit=limit, currency=currency, output=output, timeout=timeout,
        client_override=client_override, journeys=CASES, version=VERSION, checker=v1.check_step,
    )
    report.update(manifest_sha256=MANIFEST_SHA256, scorer=MANIFEST["scorer"],
                  parent_manifest_sha256=v1.MANIFEST_SHA256)
    rows = [step for result in report["results"] for step in result["steps"]]
    report["metrics"] = {
        "planned_tasks": len(CASES),
        "completed_tasks": sum(row["complete"] for row in report["results"]),
        "passed_tasks": sum(row["passed"] for row in report["results"]),
        "planned_stages": sum(map(len, CASES.values())), "executed_stages": len(rows),
        "passed_stages": sum(row["passed"] for row in rows),
        "elapsed_seconds": round(sum(row["elapsed_seconds"] for row in rows), 6),
    }
    journey._save(output.with_suffix(".json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--budget-currency", choices=("CNY", "USD"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if (not args.budget.is_finite() or args.budget <= 0 or not math.isfinite(args.timeout)
            or args.timeout <= 0):
        parser.error("positive finite budget and timeout required")
    if (path := find_dotenv(Path.cwd())) is not None:
        load_dotenv(path)
    config = replace(LLMConfig.from_env(), model=args.model)
    report = asyncio.run(run(config, limit=args.budget, currency=args.budget_currency,
                             output=args.output, timeout=args.timeout))
    raise SystemExit(0 if report["complete"] and report["source_consistent"] and all(
        row["passed"] for row in report["results"]
    ) and not report["budget"]["unknown_usage"] else 1)


if __name__ == "__main__":
    main()

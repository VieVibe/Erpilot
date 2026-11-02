"""Independent P0/P1 whole-task suite; older journey cases and scores stay frozen."""

import argparse
import asyncio
import json
import math
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMConfig

from evals import context_workflow as cw
from evals import order_journey as journey

VERSION = "order-reliability-1"


@dataclass(frozen=True)
class ReliabilityStep(journey.Step):
    budget: float | None = None
    review_codes: tuple[str, ...] | None = None


CASES = {
    "budget-denial-restore": (
        ReliabilityStep("user", journey.CREATE + "预算上限为45元。",
                        mutation="prepare_order_draft", budget=45),
        ReliabilityStep("user", journey.SUBMIT, budget=45, error="budget_exceeded",
                        mutation="submit_order_draft", review_codes=("budget_exceeded",)),
        ReliabilityStep("user", "预算改为75元；客户、商品、数量、顺序和备注不变，只保存新版本。",
                        revision=2, budget=75, mutation="revise_order_draft"),
        ReliabilityStep("user", journey.SUBMIT, revision=2, budget=75, pending=True,
                        mutation="submit_order_draft", review_codes=()),
        ReliabilityStep("restore", revision=2, budget=75, pending=True),
        ReliabilityStep("deny", revision=2, budget=75),
        ReliabilityStep("user", journey.SUBMIT, revision=2, budget=75, pending=True,
                        mutation="submit_order_draft"),
        ReliabilityStep("approve", revision=2, budget=75, ordered=True),
        ReliabilityStep("verify", journey.VERIFY, revision=2, budget=75, ordered=True),
    ),
    "price-change-review": (
        ReliabilityStep("user", journey.CREATE, mutation="prepare_order_draft"),
        ReliabilityStep("user", journey.SUBMIT, pending=True, mutation="submit_order_draft"),
        ReliabilityStep("external_price", pending=True),
        ReliabilityStep("approve", error="quote_changed"),
        ReliabilityStep("user", "只读查询当前草稿，说明待确认事项和下一步，不修改、不提交。",
                        review_codes=("quote_changed",)),
        ReliabilityStep("user", f"刷新客户{journey.CUSTOMER}当前草稿的报价，"
                        "客户、商品顺序、数量和备注不变。只刷新，不提交，等我重新确认。",
                        revision=2, mutation="revise_order_draft"),
        ReliabilityStep("user", journey.SUBMIT, revision=2, pending=True,
                        mutation="submit_order_draft", review_codes=()),
        ReliabilityStep("approve", revision=2, ordered=True),
        ReliabilityStep("verify", journey.VERIFY, revision=2, ordered=True),
    ),
    "ambiguity-and-selection": (
        ReliabilityStep("user", journey.CREATE, mutation="prepare_order_draft"),
        ReliabilityStep("user", f"给{journey.OTHER}保存报价草稿："
                        f"{cw.SKUS[0]} 2件，{cw.SKUS[1]} 3件；"
                        f"备注原文「{journey.NOTE}」。只存草稿。", other=True,
                        mutation="prepare_order_draft"),
        ReliabilityStep("user", "把第二行改成5件，先不要提交。", other=True,
                        review_codes=("draft_selection_required",)),
        ReliabilityStep("user", f"只修改客户{journey.CUSTOMER}的当前版本第二行改成5件，其他不变。",
                        revision=2, quantities=(2, 5), other=True,
                        mutation="revise_order_draft_quantity"),
        ReliabilityStep("user", journey.SUBMIT, revision=2, quantities=(2, 5), other=True,
                        pending=True, mutation="submit_order_draft"),
        ReliabilityStep("approve", revision=2, quantities=(2, 5), other=True, ordered=True),
        ReliabilityStep("verify", journey.VERIFY, revision=2, quantities=(2, 5), other=True,
                        ordered=True),
    ),
    "version-change-and-replay": (
        ReliabilityStep("user", journey.CREATE, mutation="prepare_order_draft"),
        ReliabilityStep("user", journey.SUBMIT, pending=True, mutation="submit_order_draft"),
        ReliabilityStep("external_revision", revision=2, quantities=(5, 3), pending=True),
        ReliabilityStep("approve", revision=2, quantities=(5, 3), error="draft_changed"),
        ReliabilityStep("user", journey.SUBMIT, revision=2, quantities=(5, 3), pending=True,
                        mutation="submit_order_draft"),
        ReliabilityStep("restore", revision=2, quantities=(5, 3), pending=True),
        ReliabilityStep("approve", revision=2, quantities=(5, 3), ordered=True),
        ReliabilityStep("repeat_decision", revision=2, quantities=(5, 3), ordered=True),
        ReliabilityStep("verify", journey.VERIFY, revision=2, quantities=(5, 3), ordered=True),
    ),
}
MANIFEST = {"version": VERSION, "scorer": "order-reliability-oracle-1",
            "cases": {name: [asdict(step) for step in steps] for name, steps in CASES.items()}}
MANIFEST_SHA256 = cw.digest(MANIFEST)


def check_step(step, before, after, **kwargs):
    failed = journey.check_step(step, before, after, **kwargs)
    targets = [row for row in after["order_drafts"] if row["session_id"] == journey.SESSION
               and json.loads(row["snapshot"])["customer"] == journey.CUSTOMER]
    quote = json.loads(max(targets, key=lambda row: row["revision"])["snapshot"]) if targets else {}
    if quote.get("budget_limit") != step.budget:
        failed.append("exact_budget_preserved")
    if quote.get("item_constraint", {}).get("verified") is not True:
        failed.append("item_requirements_verified")
    if step.review_codes is not None:
        reads = [json.loads(row["content"]) for row in kwargs["history"]
                 if row["ok"] and row["name"] in {"get_order_task", "get_order_draft"}]
        actual = None
        for read in reversed(reads):
            # An ambiguous task has its own selection issue, not a guessed draft.
            current = read.get("draft") if read.get("status") == "active" else read
            if isinstance(current, dict) and "pending_issues" in current:
                actual = {row["code"] for row in current["pending_issues"]}
                break
        if actual != set(step.review_codes):
            failed.append("actual_pending_issues_read")
    return failed


async def run(config, *, limit, currency, output, timeout=60, client_override=None):
    report = await journey.run_live(
        config, limit=limit, currency=currency, output=output, timeout=timeout,
        client_override=client_override, journeys=CASES, version=VERSION, checker=check_step,
    )
    report["manifest_sha256"] = MANIFEST_SHA256
    report["scorer"] = MANIFEST["scorer"]
    rows = [step for result in report["results"] for step in result["steps"]]
    report["metrics"] = {
        "planned_tasks": len(CASES),
        "completed_tasks": sum(row["complete"] for row in report["results"]),
        "passed_tasks": sum(row["passed"] for row in report["results"]),
        "planned_stages": sum(map(len, CASES.values())), "executed_stages": len(rows),
        "passed_stages": sum(row["passed"] for row in rows),
        "elapsed_seconds": round(sum(row["elapsed_seconds"] for row in rows), 6),
        "wrong_draft_stages": sum(any(code in row["failed_checks"] for code in (
            "exact_draft_versions", "target_constraints_and_all_items", "exact_budget_preserved",
        )) for row in rows),
        "wrong_order_stages": sum(any(code in row["failed_checks"] for code in (
            "exact_order_count", "exact_order_items", "exact_inventory", "no_premature_execution",
        )) for row in rows),
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

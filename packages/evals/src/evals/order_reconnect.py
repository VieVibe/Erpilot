"""Same frozen order-v2 cases/oracle, with explicitly authorized transport recovery."""

import argparse
import asyncio
import tempfile
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from agent_core.billing import BudgetedClient, endpoint_rate
from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMClient, LLMConfig
from openai import AsyncOpenAI

from evals import context_workflow as cw
from evals import order_journey as journey
from evals import order_reliability as oracle
from evals import order_reliability_v2 as v2
from evals.campaign import request_totals
from evals.reconnect import ReconnectingClient, RecoveryBudget


async def run(config, *, output, limit=Decimal("0.12"), timeout=45):
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        key: output.with_name(output.name + suffix)
        for key, suffix in (
            ("report", ".json"),
            ("requests", "-requests.jsonl"),
            ("inputs", "-inputs.jsonl"),
            ("retries", "-retries.jsonl"),
        )
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("batch already exists; preserve it and use a new path")
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != "CNY":
        raise ValueError("this acceptance allocation is CNY")
    budget = RecoveryBudget(limit, rate.currency, max_unknown=12)
    for key in ("requests", "inputs", "retries"):
        paths[key].touch(exist_ok=False)
    report = {
        "version": "order-v2-transport-recovery-1",
        "model": config.model,
        "endpoint_host": rate.endpoint_host,
        "source": cw.source_fingerprint(),
        "manifest_sha256": v2.MANIFEST_SHA256,
        "cases_sha256": cw.digest({k: [asdict(s) for s in v] for k, v in v2.CASES.items()}),
        "scorer": v2.MANIFEST["scorer"],
        "timeout": timeout,
        "sdk_retries": 0,
        "transport_attempts_per_completion": 4,
        "retry_delays_seconds": [2, 4, 8],
        "case_retries": 0,
        "partial_attempt_events": "buffered and discarded on failure",
        "authorization": "user 2026-11-04 explicitly permits reconnection after interruption",
        "fixture_controls": "Fresh synthetic ERP; shared in-memory saver across host objects, "
        "not hard process-crash recovery. Old batch is untouched.",
        "results": [],
        "complete": False,
        "passed": False,
    }

    def save():
        report["budget"] = budget.summary()
        report["journal_audit"] = request_totals(paths["requests"])
        journey._save(paths["report"], report)

    def retry(event):
        cw.append_record(paths["retries"], event)
        print(
            f"transport request {event['physical_request']}: {event['error_type']}; "
            f"retry={event['retry']}; {budget.reason()}",
            flush=True,
        )

    save()
    async with AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, timeout=timeout, max_retries=0
    ) as sdk:
        raw = LLMClient(config, client=sdk, request_timeout=timeout)
        billed = BudgetedClient(
            cw.RecordedClient(raw, paths["inputs"], budget, report["source"]),
            rate,
            budget,
            request_observer=lambda event: cw.append_record(paths["requests"], event),
        )
        client = ReconnectingClient(billed, attempts=4, delays=(2, 4, 8), observer=retry)
        for name, steps in v2.CASES.items():
            if budget.exhausted:
                break
            row = {
                "journey": name,
                "planned_steps": len(steps),
                "steps": [],
                "complete": False,
                "passed": False,
            }
            report["results"].append(row)

            def on_step(results, row=row, name=name):
                row["steps"] = results
                row["complete"] = len(results) == row["planned_steps"] and not results[-1]["error"]
                row["passed"] = row["complete"] and all(r["passed"] for r in results)
                save()
                print(
                    f"{name}/{len(results)}: "
                    f"{'PASS' if results[-1]['passed'] else results[-1]['failed_checks']}; "
                    f"known={budget.spent} CNY; unknown={budget.unknown_requests}",
                    flush=True,
                )

            client.begin_case(name)
            with tempfile.TemporaryDirectory(prefix="erpilot-reconnected-order-") as temporary:
                await journey.run_journey(
                    name,
                    client=client,
                    directory=Path(temporary),
                    trace_dir=cw.ROOT / "traces/order-journey" / output.name,
                    on_step=on_step,
                    steps=steps,
                    checker=oracle.check_step,
                )
        report["transport"] = {
            "logical_requests": client.logical_requests,
            "physical_requests": budget.requests,
            "failed_attempts": client.failed_attempts,
            "recovered_requests": client.recovered_requests,
        }
    rows = [s for result in report["results"] for s in result["steps"]]
    report["metrics"] = {
        "planned_tasks": len(v2.CASES),
        "planned_stages": sum(map(len, v2.CASES.values())),
        "completed_tasks": sum(r["complete"] for r in report["results"]),
        "passed_tasks": sum(r["passed"] for r in report["results"]),
        "executed_stages": len(rows),
        "passed_stages": sum(s["passed"] for s in rows),
        "elapsed_seconds": round(sum(s["elapsed_seconds"] for s in rows), 3),
    }
    report["source_consistent"] = report["source"]["sha256"] == cw.source_fingerprint()["sha256"]
    report["complete"] = len(report["results"]) == len(v2.CASES) and all(
        r["complete"] for r in report["results"]
    )
    report["passed"] = (
        report["complete"]
        and report["source_consistent"]
        and all(r["passed"] for r in report["results"])
    )
    report["billing_complete"] = not budget.unknown_usage
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget", type=Decimal, default=Decimal("0.12"))
    args = parser.parse_args()
    if path := find_dotenv(Path.cwd()):
        load_dotenv(path)
    config = replace(LLMConfig.from_env(), model="glm-5.3-flash")
    report = asyncio.run(run(config, output=args.output, limit=args.budget))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

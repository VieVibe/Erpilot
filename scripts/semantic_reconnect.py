"""Run complete isolated semantic probes; retry only transport-interrupted batches."""

import argparse
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSIENT = {
    "APIConnectionError",
    "APITimeoutError",
    "TimeoutError",
    "RateLimitError",
    "InternalServerError",
}
RESERVE = Decimal("0.005")


def run(output, *, revision=False, limit=Decimal("0.02")):
    output = output.resolve()
    summary = output.with_name(output.name + "-campaign.json")
    summary.parent.mkdir(parents=True, exist_ok=True)
    if summary.exists() or any(output.parent.glob(output.name + "-attempt-*")):
        raise ValueError("campaign already exists")
    result = {
        "policy": "at most three complete fresh batches; transport failures only",
        "revision": revision,
        "limit_CNY": str(limit),
        "batches": [],
        "passed": False,
        "known_spent_CNY": "0",
        "unknown_requests": 0,
        "unknown_reserve_per_request_CNY": str(RESERVE),
        "reserve_is_actual_bill": False,
    }
    summary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    known, unknown = Decimal(0), 0
    for attempt in range(1, 4):
        remaining = limit - known - RESERVE * unknown
        if remaining <= 0:
            result["stop_reason"] = "planning budget exhausted"
            break
        directory = output.with_name(output.name + f"-attempt-{attempt}")
        command = [
            sys.executable,
            "-m",
            "evals.intent_semantic_live",
            "--output",
            str(directory),
            "--limit",
            str(remaining),
            "--max-requests",
            "5",
            "--timeout",
            "45",
        ]
        if revision:
            command.append("--revision")
        environment = {**os.environ, "LLM_MODEL": "glm-5.3-flash", "PYTHONIOENCODING": "utf-8"}
        with directory.with_suffix(".txt").open("x", encoding="utf-8") as log:
            process = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        report = json.loads((directory / "report.json").read_bytes())
        known += Decimal(report["known_spent"])
        missing = sum(
            r["status"] != "settled" or not (r.get("billing") or {}).get("complete")
            for r in report["requests"]
        )
        unknown += missing
        codes = {r["rejection_code"] for r in report["attempts"] if r.get("rejection_code")}
        result["batches"].append(
            {
                "directory": str(directory.relative_to(ROOT)),
                "exit_code": process.returncode,
                "passed": report["passed"],
                "provider_requests": report["provider_requests"],
                "known_spent_CNY": report["known_spent"],
                "unknown_requests": missing,
                "rejection_codes": sorted(codes),
            }
        )
        result.update(
            known_spent_CNY=str(known),
            unknown_requests=unknown,
            unknown_reserve_total_CNY=str(RESERVE * unknown),
        )
        result["passed"] = report["passed"] and process.returncode == 0
        summary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "attempt": attempt,
                    "passed": result["passed"],
                    "known": str(known),
                    "unknown": unknown,
                    "codes": sorted(codes),
                }
            ),
            flush=True,
        )
        if result["passed"] or not (missing and codes and codes <= TRANSIENT):
            break
        if attempt < 3:
            time.sleep(4)
    summary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", action="store_true")
    args = parser.parse_args()
    result = run(args.output, revision=args.revision)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

"""Offline engineering regression for intent rules; no provider requests or old scorer changes.

The immutable fixture retains its historical status. A separate source-bound AI
review supplies current provenance, without claiming human gold or LLM accuracy.
"""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from erp_store.db import ProductRow, init_db, make_engine
from erp_store.intent_rules import analyze_rules
from erp_store.order_request import resolve_request
from sqlalchemy.orm import Session

from evals.intent_review import CATALOGUE, PREVIOUS, load_review

FIXTURE = Path(__file__).with_name("intent_core_120.json")


def run_regression():
    checked = load_review(FIXTURE)
    fixture = checked.fixture
    results, durations = [], []
    with tempfile.TemporaryDirectory() as directory:
        engine = make_engine(Path(directory) / "intent.db")
        init_db(engine)
        try:
            with Session(engine) as session:
                for product in CATALOGUE:
                    session.add(ProductRow(**product, category="测试"))
                session.commit()
                for case in fixture["cases"]:
                    started = time.perf_counter()
                    resolution = (resolve_request(session, case["text"], extended=True)
                                  if case["operation"] == "create" else None)
                    frame, expected, _, _ = analyze_rules(
                        case["text"], session_id="eval", request_id=case["id"],
                        previous=PREVIOUS if case["operation"] == "revise" else None,
                        resolution=resolution,
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    durations.append(elapsed)
                    passed = (frame.decision == case["decision"]
                              and expected == case.get("expected"))
                    results.append({"id": case["id"], "passed": passed,
                                    "decision": frame.decision, "expected_snapshot": expected,
                                    "unresolved": [r.model_dump() for r in frame.unresolved],
                                    "elapsed_ms": round(elapsed, 4)})
        finally:
            engine.dispose()
    ordered = sorted(durations)
    return {"suite": "intent-core-engineering-1", **checked.metadata(),
            "total": len(results), "passed": sum(r["passed"] for r in results),
            "false_accepts": sum(r["decision"] == "ready" and c["decision"] != "ready"
                                 for r, c in zip(results, fixture["cases"], strict=True)),
            "unnecessary_clarifications": sum(
                r["decision"] != "ready" and c["decision"] == "ready"
                for r, c in zip(results, fixture["cases"], strict=True)),
            "provider_requests": 0, "p50_ms": round(statistics.median(durations), 4),
            "p95_ms": round(ordered[int((len(ordered) - 1) * .95)], 4), "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_regression()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()

"""Opt-in, metered real-provider creation probe against an isolated catalogue.

Engineering expectations only; never confirms a form or writes a business draft.
Each output directory is single-use, including when a run is interrupted.
"""

import argparse
import asyncio
import json
import time
from contextlib import aclosing
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from agent_core.billing import endpoint_rate
from agent_core.llm import LLMClient, LLMConfig, TextDelta
from dotenv import load_dotenv
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.intent_contract import digest
from erp_store.intent_preview import request_preview
from erp_store.intent_semantic_state import IntentRequestRow, SemanticAttemptRow
from mcp_erp.intent_analysis import OrderIntentAnalyzer
from mcp_erp.intent_semantic import SemanticCoordinator, SemanticSettings
from sqlalchemy import select
from sqlalchemy.orm import Session

from evals.context_workflow import snapshot_all
from evals.report import source_provenance

CASES = [
    {"id": "basic",
     "text": "帮客户甲列份采购清单：机械键盘要四个，预算1500元，备注『不要提交😀』。",
     "expected": {"customer": "客户甲", "note": "不要提交😀", "budget_limit": 1500.0,
                  "items": [["K1", 4]]}},
    {"id": "multiple", "text": "帮客户乙列份采购清单：机械键盘要三个，光学鼠标要五个，预算2000元。",
     "expected": {"customer": "客户乙", "note": None, "budget_limit": 2000.0,
                  "items": [["K1", 3], ["M1", 5]]}},
    {"id": "missing_quantity", "text": "帮客户丙列份采购清单：机械键盘，预算800元。",
     "expected": {"customer": "客户丙", "note": None, "budget_limit": 800.0,
                  "items": [["K1", None]]}},
    {"id": "repeated_quote",
     "text": "帮客户丁列份采购清单：机械键盘要四个，备注『机械键盘四个仅供核对😀』。",
     "expected": {"customer": "客户丁", "note": "机械键盘四个仅供核对😀", "budget_limit": None,
                  "items": [["K1", 4]]}},
    {"id": "ambiguous_catalogue", "text": "帮客户戊列份采购清单：办公椅要两个。",
     "expected": {"customer": "客户戊", "note": None, "budget_limit": None,
                  "items": [[None, 2]]}},
    {"id": "conditional", "text": "如果客户确认，帮客户甲列份采购清单：机械键盘要四个。",
     "expected": None},
    {"id": "existing_draft", "text": "帮我修改已有草稿，把机械键盘改成四个。",
     "expected": None},
]

REVISION_CASES = [
    {"id": "revise_full", "text": "调整机械键盘到六个，预算改为1200元，备注改为『加急😀』。",
     "expected": {"customer": "原客户", "note": "加急😀", "budget_limit": 1200.0,
                  "items": [["K1", 6], ["M1", 3]]}},
    {"id": "revise_quantity", "text": "调整光学鼠标到五个，其他保持不变。",
     "expected": {"customer": "原客户", "note": "原备注", "budget_limit": 1000.0,
                  "items": [["K1", 2], ["M1", 5]]}},
    {"id": "revise_scalars", "text": "调整预算到800元，备注改为『先别提交😀』。",
     "expected": {"customer": "原客户", "note": "先别提交😀", "budget_limit": 800.0,
                  "items": [["K1", 2], ["M1", 3]]}},
    {"id": "revision_condition", "text": "如果客户确认，调整机械键盘到六个。", "expected": None},
    {"id": "revision_negated", "text": "别改机械键盘，预算改为1200元。", "expected": None},
    {"id": "revision_question", "text": "能不能调整机械键盘到六个？", "expected": None},
]


def preview_fields(preview):
    if not preview:
        return None
    return {**{k: preview[k] for k in ("customer", "note", "budget_limit")},
            "items": [[r["sku"], r["quantity"]] for r in preview["items"]]}


class RecordingClient:
    def __init__(self, client, directory):
        self.client, self.config, self.directory = client, client.config, directory
        self.calls = 0

    async def stream_chat(self, messages, tools=None):
        self.calls += 1
        path = self.directory / f"request-{self.calls:03}.json"
        record = {"messages": messages, "response": "", "events": []}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            async with aclosing(self.client.stream_chat(messages, tools=tools)) as events:
                async for event in events:
                    if isinstance(event, TextDelta):
                        record["response"] += event.text
                    elif type(event).__name__ != "ReasoningDelta":
                        record["events"].append({"type": type(event).__name__, **asdict(event)})
                    yield event
        finally:
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(directory, *, limit, max_requests, timeout, revision=False):
    # Never overwrite failed evidence or reset an existing ledger.
    directory.mkdir(parents=True, exist_ok=False)
    load_dotenv()
    config = LLMConfig.from_env()
    rate = endpoint_rate(config.model, config.base_url)
    before = source_provenance()
    cases = REVISION_CASES if revision else CASES
    report = {"suite": "intent-revision-live-1" if revision else "intent-semantic-live-1",
              "label_status": "engineering_expectations",
              "fixture_sha256": digest(json.dumps(cases, ensure_ascii=False, sort_keys=True)),
              "model": config.model, "endpoint_host": rate.endpoint_host,
              "currency": rate.currency, "limit": str(limit), "max_requests": max_requests,
              "source_before": before, "cases": []}
    engine = make_engine(directory / "probe.db")
    init_db(engine)
    with Session(engine) as session:
        session.add_all(ProductRow(sku=sku, name=name, category="测试", price=100, status="在售")
                        for sku, name in [("K1", "机械键盘"), ("M1", "光学鼠标"),
                                          ("C1", "办公椅"), ("C2", "办公椅")])
        if revision:
            session.add_all(StockRow(sku=sku, quantity=1000, warehouse="主仓")
                            for sku in ("K1", "M1"))
        session.commit()
    targets = {}
    if revision:
        drafts = ErpDrafts(engine)
        for case in cases:
            draft = drafts.prepare(case["id"], "原客户", [("K1", 2), ("M1", 3)], "原备注",
                                   budget_limit=1000, evidence={"contract": "draft-form-1"})
            targets[case["id"]] = {k: draft[k] for k in ("draft_id", "revision")}
        report["fixture_targets"] = targets
    initial = snapshot_all(engine)
    raw = LLMClient(config, request_timeout=timeout).without_retries()
    recorder = RecordingClient(raw, directory)
    analyzer = OrderIntentAnalyzer(engine, mode="enforce")
    coordinator = SemanticCoordinator(analyzer, SemanticSettings(
        rate, directory.name, limit, max_requests, timeout), lambda: recorder)
    try:
        for case in cases:
            if coordinator.budget.exhausted and case["expected"] is not None:
                report["cases"].append({**case, "actual": None, "preview": None,
                                        "passed": False, "status": "not_run_budget",
                                        "requests": 0, "duration_ms": 0})
                continue
            started = time.perf_counter()
            calls_before = recorder.calls
            if revision:
                await coordinator.propose_revision(case["id"], case["text"], case["id"],
                                                     targets[case["id"]])
            else:
                fallback = analyzer.resolve(case["id"], case["text"], turn_id=case["id"])
                if fallback["status"] != "resolved":
                    await coordinator.propose(case["id"], case["text"], case["id"], fallback)
            preview = request_preview(analyzer.store, case["id"])
            actual = preview_fields(preview)
            passed = actual == case["expected"]
            if case["expected"] is None:
                passed = passed and recorder.calls == calls_before
            else:
                passed = (passed and preview is not None
                          and preview.get("origin") == "semantic_proposal"
                          and recorder.calls - calls_before == 1)
                if revision:
                    passed = (passed and preview.get("target") == targets[case["id"]]
                              and preview.get("operation") == "revise")
            report["cases"].append({**case, "actual": actual, "preview": preview,
                                    "status": "passed" if passed else "failed",
                                    "passed": passed, "requests": recorder.calls - calls_before,
                                    "duration_ms": round((time.perf_counter() - started)*1000, 3)})
            print(json.dumps({"id": case["id"], "passed": passed,
                              "requests": recorder.calls - calls_before}), flush=True)
    finally:
        report["business_unchanged"] = snapshot_all(engine) == initial
        with Session(engine) as session:
            report["requests"] = [json.loads(r.payload) for r in session.scalars(
                select(IntentRequestRow).order_by(IntentRequestRow.request_number))]
            report["attempts"] = [json.loads(r.payload) for r in session.scalars(
                select(SemanticAttemptRow))]
        report.update({"known_spent": str(coordinator.budget.spent),
                       "unknown_usage": coordinator.budget.unknown_usage,
                       "provider_requests": recorder.calls, "source_after": source_provenance()})
        report["source_unchanged"] = (
            before["source_sha256"] == report["source_after"]["source_sha256"])
        report["passed"] = (len(report["cases"]) == len(cases)
                            and all(c["passed"] for c in report["cases"])
                            and report["business_unchanged"] and report["source_unchanged"]
                            and not report["unknown_usage"])
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        await raw._client.close()
        engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=Decimal, required=True,
                        help="Native currency estimate limit")
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--revision", action="store_true", help="Probe explicit-target revisions")
    args = parser.parse_args()
    result = asyncio.run(run(args.output, limit=args.limit, max_requests=args.max_requests,
                             timeout=args.timeout, revision=args.revision))
    print(json.dumps({k: result[k] for k in ("passed", "provider_requests", "known_spent",
                                           "currency", "unknown_usage")}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

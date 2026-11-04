"""Offline stateful intent load checks; observed timings, no model accuracy claim."""

import argparse
import json
import platform
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.intent_contract import PARSER_VERSION
from erp_store.intent_execution import make_evidence
from erp_store.intent_state import PendingIntentRow
from erp_store.mutations import MutationError
from mcp_erp.intent_analysis import OrderIntentAnalyzer
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from evals.context_workflow import snapshot_all
from evals.report import source_provenance


def _summary(samples):
    return {"count": len(samples), "p50_ms": round(statistics.median(samples), 4),
            "p95_ms": round(sorted(samples)[int((len(samples) - 1) * .95)], 4),
            "max_ms": round(max(samples), 4)}


def _save(engine, analyzer, sid):
    pending = analyzer.store.current(sid)
    with Session(engine) as session:
        from erp_store.intent_execution import reconstruct_creation

        _, analyzed = reconstruct_creation(session, analyzer._data(pending))
    expected = analyzed[1]
    evidence = make_evidence(sid, pending["messages"], pending=pending)
    return ErpDrafts(engine).prepare(
        sid, expected["customer"], [(r["sku"], r["quantity"]) for r in expected["items"]],
        expected["note"], budget_limit=expected["budget_limit"], evidence=evidence,
    )


def _run_case(directory, case_id):
    engine = make_engine(directory / f"{case_id}.db")
    init_db(engine)
    clock = [datetime.now(UTC)]
    analyzer = OrderIntentAnalyzer(engine, mode="enforce", clock=lambda: clock[0])
    turns, reads = [], []
    with Session(engine) as session:
        for i in range(100):
            for suffix in ("A", "B"):
                sku = f"P{i:03}{suffix}"
                session.add(ProductRow(sku=sku, name=f"商品{i:03}", category="测试",
                                       price=10, status="在售"))
                session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    before = snapshot_all(engine)

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(1)

    def resolve(sid, text, turn_id):
        started = time.perf_counter()
        result = analyzer.resolve(sid, text, turn_id=turn_id)
        turns.append((time.perf_counter() - started) * 1000)
        return result

    event.listen(engine, "before_cursor_execute", count)
    started = time.perf_counter()
    checks = {}
    try:
        if case_id in {"100_choices_and_counts", "100_counts_long_source"}:
            ambiguous = case_id == "100_choices_and_counts"
            names = [f"商品{i:03}" if ambiguous else f"SKU P{i:03}B" for i in range(100)]
            source = ("给客户甲准备订单：" + "，".join(names)
                      + "；备注原文『不要提交😀』；预算不超过10000元；只保存草稿")
            if not ambiguous:
                source += " " * (31999 - len(source))
            resolve("load", source, "initial")
            original = analyzer.store.current("load")
            for i in range(100):
                # Rebuild the host repeatedly from the actual persisted request.
                if i % 10 == 0:
                    analyzer = OrderIntentAnalyzer(engine, mode="enforce", clock=lambda: clock[0])
                if i == 50:
                    pending = analyzer.store.current("load")
                    invalid = resolve("load", "选第九个，三件" if ambiguous else "三箱", "bad")
                    checks["invalid_turn_keeps_state"] = (
                        invalid["status"] == "clarification_required"
                        and analyzer.store.current("load") == pending)
                result = resolve("load", "选第二个，三件" if ambiguous else "三件", f"reply-{i}")
                # One real turn may be resolved repeatedly without advancing the question.
                if i in {0, 50, 99}:
                    pending = analyzer.store.current("load")
                    replay = resolve("load", "选第二个，三件" if ambiguous else "三件",
                                     f"reply-{i}")
                    checks[f"replay_{i}"] = (result == replay
                                            and analyzer.store.current("load") == pending)
            expected = [{"sku": f"P{i:03}B", "quantity": 3} for i in range(100)]
            checks["all_100_items_bound"] = result.get("items") == expected
            resolve("load", "预算改为9000元", "scalar-102")
            pending = analyzer.store.current("load")
            result = resolve("load", "预算改为8000元", "overflow-103")
            checks["source_limit_atomic"] = (len(pending["messages"]) == 102
                and result["status"] == "clarification_required"
                and analyzer.store.current("load") == pending)
            checks["ttl_unchanged"] = pending["expires_at"] == original["expires_at"]
            checks["no_partial_business_write"] = snapshot_all(engine) == before
            draft = _save(engine, analyzer, "load")
            checks["full_snapshot_saved"] = (
                [{k: r[k] for k in ("sku", "quantity")} for r in draft["items"]] == expected
                and draft["budget_limit"] == 9000 and draft["note"] == "不要提交😀")
            history = ErpDrafts(engine).history("load", draft["draft_id"])
            record = history["items"][0]["change_record"]
            messages = record["intent_evidence"]["messages"]
            checks["all_sources_retained"] = (len(messages) == 102 and messages[0]["text"] == source
                and len({m["message_id"] for m in messages}) == 102)
            checks["consumed_once"] = analyzer.store.current("load") is None
            checks["replay_cannot_resave"] = resolve(
                "load", "预算改为9000元", "new-replay")["status"] == "clarification_required"
            details = {"items": 100, "accepted_messages": len(messages),
                       "source_characters": len(source), "restorations": 10}
        elif case_id == "session_expiry_catalogue":
            source = "给客户甲准备订单：商品000；只保存草稿"
            for i in range(16):
                resolve(f"s{i}", source, f"initial-{i}")
            checks["cross_session_no_context"] = resolve(
                "foreign", "选第二个，三件", "foreign")["status"] == "clarification_required"
            for i in range(8):
                result = resolve(f"s{i}", "选第二个，三件", f"reply-{i}")
                checks[f"bound_{i}"] = result.get("items") == [{"sku": "P000B", "quantity": 3}]
            with Session(engine) as session:
                session.get(ProductRow, "P000B").price = 11
                session.commit()
            for i in range(8, 12):
                checks[f"catalogue_rejected_{i}"] = resolve(
                    f"s{i}", "选第二个，三件", f"reply-{i}")["status"] == "clarification_required"
            clock[0] += timedelta(minutes=11)
            for i in range(12, 16):
                checks[f"expiry_rejected_{i}"] = resolve(
                    f"s{i}", "选第二个，三件", f"reply-{i}")["status"] == "clarification_required"
            checks["no_drafts"] = snapshot_all(engine)["order_drafts"] == []
            details = {"sessions": 16}
        else:
            resolve("race", "给客户甲准备订单：SKU P000B 三件；只保存草稿", "initial")

            # All contenders use the same issued evidence, not a newly read pending row.
            pending = analyzer.store.current("race")
            evidence = make_evidence("race", pending["messages"], pending=pending)

            def contender(_):
                try:
                    ErpDrafts(engine).prepare("race", "客户甲", [("P000B", 3)], evidence=evidence)
                    return "saved"
                except MutationError as exc:
                    return exc.code

            with ThreadPoolExecutor(max_workers=8) as executor:
                outcomes = list(executor.map(contender, range(8)))
            checks["exactly_one_save"] = outcomes.count("saved") == 1
            checks["seven_stale_rejections"] = outcomes.count("pending_intent_changed") == 7
            details = {"contenders": 8, "outcomes": outcomes}
        after = snapshot_all(engine)
        checks["stocks_unchanged"] = before["stocks"] == after["stocks"]
        checks["no_orders"] = after["orders"] == []
        with Session(engine) as session:
            statuses = list(session.scalars(select(PendingIntentRow.status)))
        return {"id": case_id, "passed": all(checks.values()), "checks": checks,
                "details": details, "turn_latency": _summary(turns), "select_count": len(reads),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 4),
                "draft_count": len(after["order_drafts"]), "pending_statuses": statuses}
    finally:
        event.remove(engine, "before_cursor_execute", count)
        engine.dispose()


def run_benchmark(*, directory=None):
    before = source_provenance()
    with tempfile.TemporaryDirectory(dir=directory) as workspace:
        cases = [_run_case(Path(workspace), case_id) for case_id in (
            "100_choices_and_counts", "100_counts_long_source", "session_expiry_catalogue",
            "concurrent_consumption",
        )]
    after = source_provenance()
    return {"suite": "intent-state-stress-engineering-1", "parser_version": PARSER_VERSION,
            "label_status": "engineering_stress_expectations", "provider_requests": 0,
            "platform": platform.platform(), "python": platform.python_version(),
            "source_before": before, "source_after": after,
            "source_unchanged": before["source_sha256"] == after["source_sha256"],
            "all_passed": all(r["passed"] for r in cases), "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    report = run_benchmark(directory=args.temp_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_passed": report["all_passed"], "cases": report["cases"]},
                     ensure_ascii=False))
    raise SystemExit(0 if report["all_passed"] and report["source_unchanged"] else 1)


if __name__ == "__main__":
    main()

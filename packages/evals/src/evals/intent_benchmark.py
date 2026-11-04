"""Local, offline intent stress comparison; engineering fixtures, no release accuracy claim."""

import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from erp_store.db import ProductRow, init_db, make_engine
from erp_store.intent_contract import PARSER_VERSION
from erp_store.intent_rules import analyze_rules
from erp_store.order_request import resolve_request
from sqlalchemy import event
from sqlalchemy.orm import Session

from evals.report import source_provenance


def _cases():
    header = "给客户甲准备订单："
    return [
        ("single", header + "商品000 两件；只保存草稿"),
        ("100_items", header + "，".join(f"商品{i:03} 两件" for i in range(100))),
        ("100_skus", header + "，".join(f"SKU P{i:03} 两件" for i in range(100))),
        ("long_note", header + "商品000 两件；备注原文『" + "原话😀" * 7900 + "』；只保存草稿"),
        ("repeated_quotes", header + "商品000 两件；备注原文『" + "“两件”" * 7000 + "』"),
        ("5000_name_matches", header + "同名商品 两件；只保存草稿"),
        ("numeric_boundary", header + "维生素B十二件；只保存草稿"),
    ]


def run_benchmark(*, iterations=20, directory=None):
    if type(iterations) is not int or not 1 <= iterations <= 1000:
        raise ValueError("iterations must be 1..1000")
    before = source_provenance()
    results = []
    with tempfile.TemporaryDirectory(dir=directory) as workspace:
        engine = make_engine(Path(workspace) / "benchmark.db")
        init_db(engine)
        reads = []

        def count(conn, cursor, statement, params, context, executemany):
            reads.append(statement)

        try:
            with Session(engine) as session:
                rows = [(f"P{i:03}", f"商品{i:03}") for i in range(100)]
                rows += [(f"D{i:05}", "同名商品") for i in range(5000)]
                rows += [("B", "维生素B"), ("B10", "维生素B十")]
                session.add_all(ProductRow(sku=sku, name=name, status="在售", price=10,
                                           category="测试") for sku, name in rows)
                session.commit()
                event.listen(engine, "before_cursor_execute", count)
                for case_id, source in _cases():
                    samples = {"legacy": [], "batched": []}
                    query_counts = {"legacy": [], "batched": []}
                    equivalent = True
                    for iteration in range(iterations + 1):
                        outputs = {}
                        # Alternate order to reduce systematic warm-cache advantage.
                        modes = ["legacy", "batched"] if iteration % 2 else ["batched", "legacy"]
                        for mode in modes:
                            reads.clear()
                            started = time.perf_counter()
                            resolution = resolve_request(session, source,
                                                         extended=mode == "batched")
                            frame, expected, _, _ = analyze_rules(
                                source, session_id="benchmark", request_id=case_id,
                                resolution=resolution,
                            )
                            elapsed = (time.perf_counter() - started) * 1000
                            outputs[mode] = (resolution, frame.decision, expected)
                            if iteration:  # Exclude the first warmup pair.
                                samples[mode].append(elapsed)
                                query_counts[mode].append(len(reads))
                        equivalent &= outputs["legacy"] == outputs["batched"]
                    results.append({"id": case_id, "source_characters": len(source),
                                    "equivalent": equivalent, "modes": {
                                        mode: {"p50_ms": round(statistics.median(values), 4),
                                               "p95_ms": round(sorted(values)[
                                                   int((len(values) - 1) * .95)], 4),
                                               "selects_min": min(query_counts[mode]),
                                               "selects_max": max(query_counts[mode])}
                                        for mode, values in samples.items()}})
        finally:
            if event.contains(engine, "before_cursor_execute", count):
                event.remove(engine, "before_cursor_execute", count)
            engine.dispose()
    after = source_provenance()
    return {"suite": "intent-stress-engineering-1", "parser_version": PARSER_VERSION,
            "label_status": "engineering_stress_expectations",
            "provider_requests": 0, "iterations_per_mode": iterations, "warmup_pairs": 1,
            "platform": platform.platform(), "python": platform.python_version(),
            "catalogue_size": 5102, "source_before": before, "source_after": after,
            "source_unchanged": before["source_sha256"] == after["source_sha256"],
            "all_equivalent": all(r["equivalent"] for r in results), "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    report = run_benchmark(iterations=args.iterations, directory=args.temp_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["all_equivalent"] and report["source_unchanged"] else 1)


if __name__ == "__main__":
    main()

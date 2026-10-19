"""Explicit, case-level continuation of interrupted read-only evaluations.

Each segment has its own immutable report and fsynced request journal. Finished
model failures are retained. Unknown charges survive continuation and prevent a
claim of fully metered acceptance, even if later attempts finish every case.
"""

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from evals.baseline import run_baseline
from evals.billing import BudgetedClient, CurrencyBudget
from evals.model import CaseResult
from evals.report import source_provenance, write_report


def _now():
    return datetime.now(UTC).isoformat()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def _lock(path):
    """OS lock is released after a crash; a stale file never authorizes concurrency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("campaign is already running") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def request_totals(path):
    requests = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        number = event["request"]
        if event["status"] == "started":
            if number in requests:
                raise ValueError("duplicate request in campaign journal")
            requests[number] = event
        elif number not in requests or requests[number]["status"] != "started":
            raise ValueError("invalid request settlement in campaign journal")
        else:
            requests[number] = event
    known, unknown = Decimal(0), 0
    for event in requests.values():
        billing = event.get("billing")
        if event["status"] == "started" or not billing or billing["estimated_amount"] is None:
            unknown += 1
        else:
            known += Decimal(billing["estimated_amount"])
    return {"requests": len(requests), "known_spent": str(known), "unknown_requests": unknown}


def _case_status(row):
    if any(c.startswith(("not_run:", "budget:")) for c in row["failed_checks"]):
        return "not_run"
    if row.get("failure_kind") == "upstream_interrupted":
        return "upstream_interrupted"
    if row.get("error"):
        return "runtime_error"
    # A max-step/model failure is a finished observation, never a free retry.
    return "passed" if row["passed"] else "model_failed"


def summarize_campaign(manifest):
    selected, attempts = {}, []
    known, unknown, requests = Decimal(0), 0, 0
    source_consistent = True
    for segment in manifest["segments"]:
        totals = request_totals(segment["journal"])
        known += Decimal(totals["known_spent"])
        unknown += totals["unknown_requests"]
        requests += totals["requests"]
        report_path = segment.get("report")
        if not report_path:
            continue
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        source_consistent &= (
            report["provenance"]["source_sha256"] == manifest["identity"]["source_sha256"]
            and not report["suite"].get("source_changed_during_run", False)
        )
        for row in report["results"]:
            status = _case_status(row)
            attempts.append({"segment": segment["id"], "case_id": row["case_id"],
                             "status": status, "result": row})
            if status not in {"not_run", "upstream_interrupted"}:
                if row["case_id"] in selected:
                    raise ValueError("campaign must not repeat a finished case")
                selected[row["case_id"]] = attempts[-1]
    scope = manifest["identity"]["case_ids"]
    pending = [cid for cid in scope if cid not in selected]
    complete = not pending
    passed = sum(item["status"] == "passed" for item in selected.values())
    return {
        "planned": len(scope), "finished": len(selected), "passed": passed,
        "pending_case_ids": pending, "complete_scope": complete,
        "all_cases_passed": complete and passed == len(scope),
        "billing_complete": unknown == 0, "known_spent": str(known),
        "currency": manifest["currency"], "unknown_requests": unknown,
        "requests": requests, "attempts": attempts,
        "selected_results": {cid: item for cid, item in selected.items()},
        "source_consistent": source_consistent,
        "acceptance_passed": (
            complete and passed == len(scope) and unknown == 0 and source_consistent
        ),
    }


def export_campaign(manifest, path, cases):
    """Derived full-scope report; raw attempts and unknown billing remain attached."""
    summary = manifest["summary"]
    reports = {
        segment["id"]: json.loads(Path(segment["report"]).read_text(encoding="utf-8"))
        for segment in manifest["segments"] if segment.get("report")
    }
    if not reports:
        return
    first = next(iter(reports.values()))
    rows, traces = [], {}
    for case in cases:
        selected = summary["selected_results"].get(case.id)
        observed = [a for a in summary["attempts"] if a["case_id"] == case.id
                    and a["status"] != "not_run"]
        item = selected or (observed[-1] if observed else None)
        if item is None:
            rows.append(CaseResult(case_id=case.id, category=case.category, passed=False,
                                   failed_checks=["not_run: pending campaign continuation"]))
        else:
            rows.append(CaseResult.model_validate(item["result"]))
            trace = reports[item["segment"]]["suite"]["case_traces"].get(case.id)
            if trace:
                traces[case.id] = trace
    metadata = {**first["suite"],
        "kind": "campaign-aggregate", "case_ids": manifest["identity"]["case_ids"],
        "case_sha256": manifest["identity"]["case_sha256"], "planned": len(cases),
        "executed": sum(not any(c.startswith("not_run:") for c in r.failed_checks) for r in rows),
        "complete": summary["complete_scope"], "case_traces": traces,
        "source_changed_during_run": not summary["source_consistent"],
        "state_checks": len(cases),
        "billing_budget": {
            "currency": manifest["currency"], "limit": manifest["limit"],
            "spent": summary["known_spent"], "requests": summary["requests"],
            "unknown_usage": not summary["billing_complete"],
        },
        "campaign": {"manifest": str(path), "segments": manifest["segments"],
                     "unknown_requests": summary["unknown_requests"],
                     "finished": summary["finished"], "no_completed_case_reruns": True},
    }
    write_report(
        path.parent, rows, model=manifest["identity"]["model"], metadata=metadata,
        provenance=first["provenance"], report_path=path.with_name(path.stem + "-summary.md"),
        elapsed_s=sum(
            (datetime.fromisoformat(s["finished_at"]) - datetime.fromisoformat(s["started_at"]))
            .total_seconds() for s in manifest["segments"] if s.get("finished_at")
        ),
    )


async def run_campaign(
    cases, *, client, rate, limit, manifest_path, case_harness,
    report_dir, trace_dir, resume=False, allow_unknown=False,
    case_limit=None, segment_budget=None,
):
    from evals.grounding_cases import GroundingCase

    if not cases or not all(isinstance(case, GroundingCase) for case in cases):
        raise ValueError("campaign continuation currently supports isolated grounding cases only")
    if case_limit is not None and case_limit <= 0:
        raise ValueError("case_limit must be positive")
    if not limit.is_finite() or limit <= 0:
        raise ValueError("campaign limit must be finite and positive")
    if segment_budget is not None and (not segment_budget.is_finite() or segment_budget <= 0):
        raise ValueError("segment budget must be finite and positive")
    sdk = getattr(client, "_client", None)
    if sdk is not None and getattr(sdk, "max_retries", 0) != 0:
        raise ValueError("campaign requires SDK max_retries=0")
    path = Path(manifest_path).resolve()
    identity = {
        "case_ids": [case.id for case in cases],
        "case_sha256": hashlib.sha256(
            "\n".join(c.model_dump_json() for c in cases).encode()
        ).hexdigest(),
        "scorers": sorted({c.scorer_version for c in cases}),
        "source_sha256": source_provenance()["source_sha256"],
        "model": client.config.model,
        "thinking": getattr(client.config, "thinking", False),
        "base_url_sha256": hashlib.sha256(client.config.base_url.encode()).hexdigest(),
        "timeout": client.request_timeout,
        "rate": json.loads(json.dumps(asdict(rate), default=str)),
        "fixture_sha256": case_harness.policy_index_sha256,
        "corpus_sha256": case_harness.policy_corpus_sha256,
    }
    with _lock(path.with_suffix(path.suffix + ".lock")):
        if path.exists():
            if not resume:
                raise ValueError("campaign exists; use explicit --resume-campaign")
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["identity"] != identity or Decimal(manifest["limit"]) != limit:
                raise ValueError("campaign source, scope, model, fixture, rate or cap changed")
            for segment in manifest["segments"]:
                for key in ("report", "journal"):
                    if (segment.get(key + "_sha256")
                            and _sha(segment[key]) != segment[key + "_sha256"]):
                        raise ValueError("campaign segment evidence changed")
                if segment["status"] == "running":
                    segment.update(status="interrupted_process", finished_at=_now())
                    segment["journal_sha256"] = _sha(segment["journal"])
                    if segment.get("report"):
                        segment["report_sha256"] = _sha(segment["report"])
            summary = summarize_campaign(manifest)
            if not summary["source_consistent"]:
                raise ValueError("campaign contains a source-changing segment")
            # Persist crash recovery even when no new request may be issued.
            manifest["summary"] = summary
            _save(path, manifest)
            if summary["unknown_requests"] and not allow_unknown:
                raise ValueError(
                    "previous segments contain unknown charges; an explicit "
                    "--resume-with-unknown starts a new bounded segment and retains them"
                )
        else:
            if resume:
                raise ValueError("campaign does not exist")
            manifest = {
                "schema_version": 1, "created_at": _now(), "identity": identity,
                "limit": str(limit), "currency": rate.currency, "segments": [],
                "unknown_charge_policy": "freeze-segment; explicit continuation; never erase",
            }
            summary = summarize_campaign(manifest)
        pending = summary["pending_case_ids"]
        if not pending:
            return summary, path
        remaining = limit - Decimal(summary["known_spent"])
        request_limit = max(40, 8 * len(cases)) - summary["requests"]
        if remaining <= 0 or request_limit <= 0:
            raise ValueError("campaign budget or request allowance exhausted")
        selected_ids = pending[:case_limit] if case_limit else pending
        selected = [c for c in cases if c.id in selected_ids]
        segment_id = f"{len(manifest['segments']) + 1:03d}-{uuid4().hex[:6]}"
        journal = path.parent / f"{path.stem}-{segment_id}-requests.jsonl"
        journal.touch(exist_ok=False)
        segment = {
            "id": segment_id, "started_at": _now(), "status": "running",
            "case_ids": selected_ids, "journal": str(journal), "report": None,
            "limit": str(min(remaining, segment_budget or remaining)),
            "resumed_with_unknown": bool(summary["unknown_requests"]),
        }
        manifest["segments"].append(segment)
        _save(path, manifest)

        def observe(event):
            with journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        def progress(report):
            segment["report"] = str(report.with_suffix(".json").resolve())
            manifest["summary"] = summarize_campaign(manifest)
            _save(path, manifest)

        budget = CurrencyBudget(Decimal(segment["limit"]), rate.currency,
                                max_requests=request_limit)
        wrapped = BudgetedClient(client, rate, budget, request_observer=observe)
        try:
            await run_baseline(
                selected, client=wrapped, currency_budget=budget, case_harness=case_harness,
                report_dir=report_dir, trace_dir=trace_dir, progress=progress,
            )
            segment["status"] = "frozen_unknown_usage" if budget.unknown_usage else "closed"
        except BaseException:
            segment["status"] = "interrupted_process"
            raise
        finally:
            segment["finished_at"] = _now()
            segment.update(request_totals(journal))
            segment["journal_sha256"] = _sha(journal)
            if segment.get("report"):
                segment["report_sha256"] = _sha(segment["report"])
            manifest["summary"] = summarize_campaign(manifest)
            _save(path, manifest)
            export_campaign(manifest, path, cases)
        return manifest["summary"], path

"""Local intent observations. Never supply evidence or affect tool execution."""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from agent_core.events import (
    ApprovalPending,
    ApprovalResolved,
    LoopEnd,
    ReconciliationPending,
    StepEnd,
    StepStarted,
    ToolCallFinished,
    ToolCallStarted,
    ToolExecuting,
)
from erp_store.intent_contract import CONTRACT, PARSER_VERSION, POLICY_VERSION, digest

_LOG = logging.getLogger(__name__)


class IntentDiagnostics:
    def __init__(self, mode):
        self.mode = mode
        self._active = {}

    def begin(self, session_id, messages, path, *, kind, model, run_id):
        if self.mode == "off":
            return None
        request = _Request(path, self.mode, session_id)
        self._active[session_id] = request
        users = [m["content"] for m in messages if m.get("role") == "user"]
        request.write("request_start", {
            "kind": kind, "run_id": run_id, "model": model,
            "parser_version": PARSER_VERSION, "policy_version": POLICY_VERSION,
            "contract": CONTRACT, "candidate_source": "rules", "intent_model_requests": 0,
            "prompt_sha256": digest(json.dumps(
                [m for m in messages if m.get("role") == "system"], ensure_ascii=False)),
            "turn_id": digest(json.dumps(users, ensure_ascii=False)),
            "input_refs": [{"user_index": i, "sha256": digest(s)} for i, s in enumerate(users)],
            "current_source": {"text": users[-1], "sha256": digest(users[-1])} if users else None,
        })
        return request

    def record(self, session_id, kind, data):
        if request := self._active.get(session_id):
            request.write(kind, data)

    def policy(self, session_id, history, name, allowed):
        if request := self._active.get(session_id):
            phase = digest(json.dumps(history, ensure_ascii=False, sort_keys=True))
            request.policies.setdefault(phase, {})[name] = allowed

    def finish(self, session_id, request, *, error=None):
        if request is None:
            return
        try:
            request.finish(error)
        finally:
            if self._active.get(session_id) is request:
                del self._active[session_id]


class _Request:
    def __init__(self, path, mode, session_id):
        self.path, self.mode, self.session_id = path, mode, session_id
        self.request_id, self.seq = uuid4().hex, 0
        self.started = time.perf_counter()
        self.policies, self.emitted_policies = {}, {}
        self.model_steps, self.usage_steps = set(), {}
        self.outcome, self.final_versions = None, []
        self.failed = False
        self.rule_analyses = 0
        self.intent_requests, self.billing_records = 0, {}

    def write(self, kind, data):
        if self.failed:
            return
        try:
            self.seq += 1
            if kind in {"rule_resolution", "shadow_analysis"}:
                self.rule_analyses += 1
            if kind == "model_billing":
                if data["status"] == "started" and data["purpose"] == "intent":
                    self.intent_requests += 1
                self.billing_records[data["request"]] = data
            row = {"schema_version": 1, "request_id": self.request_id,
                   "session_id": self.session_id, "mode": self.mode, "seq": self.seq,
                   "ts": datetime.now(UTC).isoformat(), "event": kind, "data": data}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            # Diagnostics are observations, never a new business precondition.
            self.failed = True
            _LOG.warning("intent_diagnostics_unavailable request_id=%s", self.request_id)

    def flush_policies(self):
        for phase, permissions in self.policies.items():
            if permissions != self.emitted_policies.get(phase):
                self.write("stage_permissions", {
                    "phase_sha256": phase,
                    "allowed_next_tools": sorted(k for k, v in permissions.items() if v),
                    "blocked_tools": sorted(k for k, v in permissions.items() if not v),
                })
                self.emitted_policies[phase] = dict(permissions)

    def observe(self, event):
        self.flush_policies()
        if isinstance(event, StepStarted):
            self.model_steps.add(event.step)
            self.write("business_model_start", {"step": event.step})
        elif isinstance(event, StepEnd):
            usage = asdict(event.usage) if event.usage is not None else None
            self.usage_steps[event.step] = usage
            self.write("business_model_end", {"step": event.step, "usage": usage,
                                               "duration_ms": event.duration_ms})
        elif isinstance(event, ToolCallStarted):
            self.write("tool_attempt", asdict(event.call))
        elif isinstance(event, ToolExecuting):
            self.write("tool_executing", asdict(event))
        elif isinstance(event, ToolCallFinished):
            try:
                result = json.loads(event.content)
            except (ValueError, TypeError):
                result = None
            error = result.get("error") if isinstance(result, dict) else None
            code = (error.get("code") or error.get("type")) if isinstance(error, dict) else None
            self.write("tool_result", {"call_id": event.call_id, "name": event.name,
                                       "ok": event.ok, "rejection_code": code,
                                       "content": event.content})
            if (event.ok and event.name in {"prepare_order_draft", "revise_order_draft",
                                           "revise_order_draft_quantity"}
                    and isinstance(result, dict) and "draft_id" in result and "revision" in result):
                self.final_versions.append({"draft_id": result["draft_id"],
                                            "revision": result["revision"]})
        elif isinstance(event, (ApprovalPending, ApprovalResolved, ReconciliationPending)):
            self.write(type(event).__name__, asdict(event))
        elif isinstance(event, LoopEnd):
            self.outcome = "completed" if event.completed else "max_steps"

    def finish(self, error):
        self.flush_policies()
        unknown = sorted(s for s in self.model_steps if self.usage_steps.get(s) is None)
        interrupted = error is None or isinstance(error, (GeneratorExit, asyncio.CancelledError))
        billings = [r.get("billing") for r in self.billing_records.values()]
        known = [b for b in billings if b and b.get("estimated_amount") is not None]
        currencies = {b["currency"] for b in known}
        self.write("request_end", {
            "outcome": self.outcome or ("interrupted" if interrupted else "error"),
            "error_type": type(error).__name__ if error else None,
            "duration_ms": round((time.perf_counter() - self.started) * 1000, 4),
            "final_versions": self.final_versions, "intent_model_requests": self.intent_requests,
            "business_model_requests": len(self.model_steps), "unknown_usage_steps": unknown,
            "usage_by_step": self.usage_steps,
            "rule_analysis_events": self.rule_analyses,
            "rule_analysis_status": "observed" if self.rule_analyses else "not_requested",
            "billing_source": "existing_trace_and_budget_protocol",
            "metered_requests": list(self.billing_records.values()),
            "metered_known_amounts": {currency: str(sum(
                (Decimal(b["estimated_amount"]) for b in known if b["currency"] == currency),
                Decimal(0))) for currency in sorted(currencies)},
            "metered_unknown_requests": sum(not b or b.get("estimated_amount") is None
                                             for b in billings),
        })

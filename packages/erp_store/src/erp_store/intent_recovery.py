"""Suspend only new pending intent state on rollback; never migrate saved drafts or approvals."""

import re
from datetime import UTC, datetime

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from erp_store.intent_state import PendingIntentRow
from erp_store.mutations import ErpMutations

RECHECK_MESSAGE = "此请求已因模式切换停用，请重新说明完整要求并重新核对，不能沿用旧候选或确认。"


class IntentRecovery(ErpMutations):
    def __init__(self, engine, *, clock=None):
        super().__init__(engine)
        self.clock = clock or (lambda: datetime.now(UTC))
        # A legacy off database must not gain intent tables just by being opened.
        self.available = inspect(engine).has_table(PendingIntentRow.__tablename__)

    def suspend(self):
        if not self.available:
            return
        with self._write_session() as session:
            rows = session.scalars(select(PendingIntentRow).where(
                PendingIntentRow.status.in_(["pending", "ready"])))
            for row in rows:
                row.status = "needs_recheck"
                row.version += 1
            session.commit()

    def preview(self, session_id):
        if not self.available:
            return None
        with Session(self._engine) as session:
            row = session.scalar(select(PendingIntentRow).where(
                PendingIntentRow.session_id == session_id,
            ).order_by(PendingIntentRow.created_at.desc(), PendingIntentRow.pending_id).limit(1))
            if row is None or row.status != "needs_recheck":
                return None
            expired = datetime.fromisoformat(row.expires_at) <= self.clock()
            return {"schema_version": 1, "pending_id": row.pending_id,
                    "state_version": row.version, "status": "needs_recheck",
                    "expires_at": row.expires_at, "can_confirm": False,
                    "clarification_attempts": 0, "customer": "", "note": None,
                    "budget_limit": None, "items": [],
                    "message": "原请求已过期，请重新说明完整要求。" if expired else RECHECK_MESSAGE}

    def view(self, session_id):
        preview = self.preview(session_id)
        if preview is None:
            return None
        return {**{k: preview[k] for k in ("pending_id", "state_version", "status", "expires_at")},
                "preserved_fields": {}, "question": preview["message"], "candidates": [],
                "clarification_attempts": 0}

    def blocks_reply(self, session_id, source):
        if self.preview(session_id) is None:
            return False
        from erp_store.intent_rules import quantity_reply, selection_reply, slot_reply

        return bool(selection_reply(source) or slot_reply(source) is not None
                    or quantity_reply(source) is not None
                    or re.fullmatch(r"\s*(?:确认|好的?|可以|继续|照旧|就这样|SKU\s+\S+)"
                                    r"\s*[。！!]?", source))

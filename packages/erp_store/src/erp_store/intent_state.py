"""Durable intent metadata, separate from quotes; consumed in the quote transaction."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import String, Text, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from erp_store.intent_contract import digest
from erp_store.mutations import ErpMutations, MutationError


class IntentBase(DeclarativeBase):
    pass


class PendingIntentRow(IntentBase):
    __tablename__ = "pending_intents"

    pending_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[str] = mapped_column(String(40))


class IntentClarificationRow(IntentBase):
    __tablename__ = "intent_clarifications"

    pending_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    state_version: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(128), primary_key=True)


def payload(row):
    return {"pending_id": row.pending_id, "state_version": row.version,
            "status": row.status, "expires_at": row.expires_at,
            **json.loads(row.payload)}


def require_pending(session, session_id, pending_id, version, now, *, statuses=("ready",)):
    row = session.get(PendingIntentRow, pending_id)
    if (row is None or row.session_id != session_id or row.version != version
            or row.status not in statuses or datetime.fromisoformat(row.expires_at) <= now):
        raise MutationError("pending_intent_changed", "待完成请求已变化、过期或已消费，请重新核对")
    return row


class IntentStore(ErpMutations):
    def __init__(self, engine, *, clock=None, ttl_seconds=600):
        super().__init__(engine)
        self.clock = clock or (lambda: datetime.now(UTC))
        if ttl_seconds <= 0:
            raise ValueError("intent TTL must be positive")
        self.ttl_seconds = ttl_seconds
        if any(not inspect(engine).has_table(t.name) for t in IntentBase.metadata.sorted_tables):
            with self._write_session() as session:
                IntentBase.metadata.create_all(session.connection())
                session.commit()

    def now(self):
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("intent clock must include timezone")
        return now.astimezone(UTC)

    def current(self, session_id):
        # Page restoration never updates, consumes or executes this request.
        with Session(self._engine) as session:
            rows = list(session.scalars(select(PendingIntentRow).where(
                PendingIntentRow.session_id == session_id,
                PendingIntentRow.status.in_(["pending", "ready"]),
            )))
            if len(rows) != 1:
                return None
            result = payload(rows[0])
            if datetime.fromisoformat(result["expires_at"]) <= self.now():
                result["status"] = "expired"
            return result

    def replace(self, session_id, data, *, status, expected_empty=False):
        with self._write_session() as session:
            now = self.now()
            rows = list(session.scalars(select(PendingIntentRow).where(
                PendingIntentRow.session_id == session_id,
                PendingIntentRow.status.in_(["pending", "ready"]),
            )))
            if expected_empty and any(datetime.fromisoformat(r.expires_at) > now for r in rows):
                raise MutationError("pending_intent_changed", "待完成请求已变化，请重新核对")
            for row in rows:
                row.status = ("expired" if datetime.fromisoformat(row.expires_at) <= now
                              else "superseded")
                row.version += 1
            row = PendingIntentRow(pending_id=uuid4().hex, session_id=session_id,
                                   version=1, status=status,
                                   payload=json.dumps(data, ensure_ascii=False),
                                   created_at=now.isoformat(),
                                   expires_at=(now + timedelta(
                                       seconds=self.ttl_seconds)).isoformat())
            session.add(row)
            session.flush()
            result = payload(row)
            session.commit()
            return result

    def update(self, session_id, pending_id, version, data, *, status):
        with self._write_session() as session:
            row = session.get(PendingIntentRow, pending_id)
            if (row is None or row.session_id != session_id or row.version != version
                    or row.status not in {"pending", "ready"}
                    or datetime.fromisoformat(row.expires_at) <= self.now()):
                raise MutationError("pending_intent_changed", "待完成请求已变化，请重新核对")
            row.status, row.version = status, row.version + 1
            row.payload = json.dumps(data, ensure_ascii=False)
            session.flush()
            result = payload(row)
            session.commit()
            return result

    def abandon_unrelated(self, session_id, source):
        from erp_store.intent_rules import WITHDRAW, selection_reply, slot_reply

        current = self.current(session_id)
        if (current and current["status"] in {"pending", "ready"}
                and source != current["messages"][-1]["text"]
                and not selection_reply(source) and slot_reply(source) is None
                and not WITHDRAW.fullmatch(source)):
            data = {k: v for k, v in current.items() if k not in {
                "pending_id", "state_version", "status", "expires_at",
            }}
            self.update(session_id, current["pending_id"], current["state_version"], data,
                        status="superseded")

    def view(self, session_id):
        pending = self.current(session_id)
        if pending is None:
            return None
        result = {key: pending[key] for key in (
            "pending_id", "state_version", "status", "expires_at", "preserved_fields",
        )}
        if pending.get("semantic_preview_version") == 1:
            return {**result, "question": "请在业务面板核对原话与完整清单后确认。",
                    "candidates": [], "clarification_attempts": 0}
        from erp_store.intent_execution import replay_selection_messages

        _, remaining = replay_selection_messages(pending)
        if remaining:
            from erp_store.intent_continuation import question_fields

            result.update(question_fields(remaining[0]))
        result["clarification_attempts"] = self.clarification_attempts(pending)
        return result

    def clarification_attempts(self, pending):
        from sqlalchemy import func

        with Session(self._engine) as session:
            return session.scalar(select(func.count()).select_from(IntentClarificationRow).where(
                IntentClarificationRow.pending_id == pending["pending_id"],
                IntentClarificationRow.state_version == pending["state_version"],
            ))

    def record_clarification(self, session_id, pending, source, turn_id=None):
        """Rejected turns do not alter the accepted request or extend its TTL."""
        with self._write_session() as session:
            require_pending(session, session_id, pending["pending_id"], pending["state_version"],
                            self.now(), statuses=("pending", "ready"))
            key = (pending["pending_id"], pending["state_version"], turn_id or digest(source))
            if session.get(IntentClarificationRow, key) is None:
                session.add(IntentClarificationRow(pending_id=key[0], state_version=key[1],
                                                   turn_id=key[2]))
            session.commit()
        return self.clarification_attempts(pending)

    def recent_slot_consumed(self, session_id, source):
        """A consumed supplement cannot silently become an edit of the saved quote.

        The original request's TTL bounds this conservative replay window. An
        active new request takes precedence at the application boundary.
        """
        with Session(self._engine) as session:
            row = session.scalar(select(PendingIntentRow).where(
                PendingIntentRow.session_id == session_id,
            ).order_by(PendingIntentRow.created_at.desc(), PendingIntentRow.pending_id).limit(1))
            return bool(row and row.status == "consumed"
                        and datetime.fromisoformat(row.expires_at) > self.now()
                        and any(m["text"] == source
                                for m in json.loads(row.payload)["messages"][1:]))


def source_message(text, message_id=None):
    return {"message_id": message_id or uuid4().hex, "text": text, "sha256": digest(text)}

"""Durable semantic-attempt deduplication and native-currency request journal."""

import json
from datetime import UTC, datetime

from sqlalchemy import String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from erp_store.mutations import ErpMutations


class SemanticBase(DeclarativeBase):
    pass


class SemanticAttemptRow(SemanticBase):
    __tablename__ = "intent_semantic_attempts"
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


class IntentBudgetRow(SemanticBase):
    __tablename__ = "intent_model_budgets"
    budget_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    configuration: Mapped[str] = mapped_column(Text)


class IntentRequestRow(SemanticBase):
    __tablename__ = "intent_model_requests"
    budget_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_number: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


class SemanticJournal(ErpMutations):
    def __init__(self, engine, budget_id, configuration):
        super().__init__(engine)
        self.budget_id = budget_id
        if not isinstance(budget_id, str) or not 1 <= len(budget_id) <= 128:
            raise ValueError("semantic budget_id must be 1..128 characters")
        encoded = json.dumps(configuration, sort_keys=True)
        with self._write_session() as session:
            SemanticBase.metadata.create_all(session.connection())
            row = session.get(IntentBudgetRow, budget_id)
            if row is not None and row.configuration != encoded:
                raise ValueError("既有语义预算配置不可覆盖，请使用独立预算标识")
            if row is None:
                session.add(IntentBudgetRow(budget_id=budget_id, configuration=encoded))
            entries = list(session.scalars(select(IntentRequestRow).where(
                IntentRequestRow.budget_id == budget_id).order_by(IntentRequestRow.request_number)))
            self.requests = [(entry.request_number, json.loads(entry.payload)) for entry in entries]
            session.commit()

    def claim(self, session_id, turn_id, source_sha256):
        with self._write_session() as session:
            row = session.get(SemanticAttemptRow, (session_id, turn_id))
            if row:
                saved = json.loads(row.payload)
                if saved["source_sha256"] != source_sha256:
                    raise ValueError("语义轮次来源已变化")
                return False
            session.add(SemanticAttemptRow(session_id=session_id, turn_id=turn_id,
                payload=json.dumps({"status": "started", "source_sha256": source_sha256,
                                    "ts": datetime.now(UTC).isoformat()})))
            session.commit()
            return True

    def finish_attempt(self, session_id, turn_id, data):
        with self._write_session() as session:
            row = session.get(SemanticAttemptRow, (session_id, turn_id))
            row.payload = json.dumps({**json.loads(row.payload), **data}, ensure_ascii=False)
            session.commit()

    def observe_request(self, record, *, session_id, purpose, usage, turn_id, source_sha256):
        with self._write_session() as session:
            key = (self.budget_id, record["request"])
            row = session.get(IntentRequestRow, key)
            value = {**record, "session_id": session_id, "purpose": purpose, "usage": usage,
                     "turn_id": turn_id, "source_sha256": source_sha256}
            if record["status"] == "started":
                if row is not None:
                    raise ValueError("该预算请求编号已占用，不能重复发出请求")
                session.add(IntentRequestRow(budget_id=key[0], request_number=key[1],
                                             payload=json.dumps(value, ensure_ascii=False)))
            else:
                if row is None:
                    raise ValueError("预算请求缺少原始占用记录")
                row.payload = json.dumps(value, ensure_ascii=False)
            session.commit()

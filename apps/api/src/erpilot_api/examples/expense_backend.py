"""Local HTTP test ERP. Run with uvicorn on loopback; persistence is SQLite."""

import hmac
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import Field

from erpilot_api.examples.expense import ExpenseInput


class Submission(ExpenseInput):
    session_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    actor_role: str | None = Field(default=None, max_length=64)
    client_token: str = Field(min_length=1, max_length=128)


def create_app(database=None, service_token=None):
    path = Path(database or os.getenv("ERPILOT_EXPENSE_DB", "data/expense-example.db"))
    expected_token = service_token or os.getenv("ERPILOT_EXPENSE_BACKEND_TOKEN") or None
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(expenses)").fetchall()
        }
        if columns and "actor_id" not in columns:
            connection.execute("ALTER TABLE expenses RENAME TO expenses_legacy")
            connection.execute(
                "CREATE TABLE expenses (actor_id TEXT, session_id TEXT, token TEXT, "
                "payload TEXT NOT NULL, PRIMARY KEY (actor_id, session_id, token))"
            )
            connection.execute(
                "INSERT INTO expenses(actor_id,session_id,token,payload) "
                "SELECT 'local',session_id,token,payload FROM expenses_legacy"
            )
            connection.execute("DROP TABLE expenses_legacy")
        elif not columns:
            connection.execute(
                "CREATE TABLE expenses (actor_id TEXT, session_id TEXT, token TEXT, "
                "payload TEXT NOT NULL, PRIMARY KEY (actor_id, session_id, token))"
            )
    app = FastAPI(title="Local expense ERP example")

    def authorize(authorization: str | None = Header(default=None)):
        if expected_token and not hmac.compare_digest(
            authorization or "", f"Bearer {expected_token}",
        ):
            raise HTTPException(
                401, "invalid service credential", headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/healthz", dependencies=[Depends(authorize)])
    def health():
        return {"status": "ready"}

    @app.post("/expenses", dependencies=[Depends(authorize)])
    def submit(request: Submission):
        with sqlite3.connect(path, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM expenses "
                "WHERE actor_id=? AND session_id=? AND token=?",
                (request.actor_id, request.session_id, request.client_token),
            ).fetchone()
            if row:
                result = json.loads(row[0])
                if (result["purpose"] != request.purpose
                        or result["amount_cents"] != request.amount_cents):
                    raise HTTPException(409, "request token already used for different content")
                return result
            result = {
                "expense_id": uuid4().hex, "purpose": request.purpose,
                "amount_cents": request.amount_cents, "client_token": request.client_token,
                "submitted_by": request.actor_id,
                "status": "pending_erp_approval",
            }
            connection.execute(
                "INSERT INTO expenses VALUES (?, ?, ?, ?)",
                (request.actor_id, request.session_id, request.client_token,
                 json.dumps(result)),
            )
            return result

    @app.get("/expenses", dependencies=[Depends(authorize)])
    def query(actor_id: str = Query(min_length=1, max_length=128),
              session_id: str = Query(min_length=1, max_length=128),
              client_token: str = Query(min_length=1, max_length=128)):
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT payload FROM expenses "
                "WHERE actor_id=? AND session_id=? AND token=?",
                (actor_id, session_id, client_token),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "expense not found")
        return json.loads(row[0])

    return app

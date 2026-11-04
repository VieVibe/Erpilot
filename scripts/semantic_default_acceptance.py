"""Real HTTP acceptance of the default-on semantic startup and business permissions.

Uses isolated synthetic ERP/accounts. SDK automatic retries are disabled so every
physical request can be recorded; a failed whole batch remains a separate report.
"""

import argparse
import asyncio
import json
import os
import secrets
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx2
from agent_core.billing import BudgetedClient, endpoint_rate
from agent_core.dotenv import load_dotenv
from agent_core.llm import LLMClient, LLMConfig
from erp_store.db import make_engine
from erp_store.intent_semantic_state import IntentRequestRow, SemanticAttemptRow
from erp_store.repository import ErpRepository
from erpilot_api.auth_store import AuthStore
from erpilot_api.bootstrap import build_live_app
from erpilot_api.offline import demo_policy_resolver, seed_demo_database
from evals import context_workflow as cw
from evals.campaign import request_totals
from evals.reconnect import RecoveryBudget
from evals.report import source_provenance
from langgraph.checkpoint.memory import InMemorySaver
from live_api_acceptance import local_server, post_stream
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER = "真实语义验收客户"
MESSAGE = (
    f"帮{CUSTOMER}列份采购清单：青瓷茶具要两个，宣纸礼盒要三个；预算500元；备注原文『分箱发货😀』。"
)
REVISION = "调整宣纸礼盒到四个，备注改为『加急😀』。"


class AcceptanceClient:
    """Outer physical-request allocation, separate from the product candidate ledger."""

    def __init__(self, client):
        self.client, self.config = client, client.config

    def stream_chat(self, messages, tools=None):
        return self.client.stream_chat(messages, tools=tools)


def confirmed_fields(preview):
    return {
        "pending_ref": {key: preview[key] for key in ("pending_id", "state_version")},
        **({"target": preview["target"]} if "target" in preview else {}),
        "customer": preview["customer"],
        "note": preview["note"],
        "budget_limit": preview["budget_limit"],
        "items": [{"sku": row["sku"], "quantity": row["quantity"]} for row in preview["items"]],
    }


async def run(output, limit=Decimal("0.05")):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        name: output.with_name(output.name + suffix)
        for name, suffix in (
            ("report", ".json"),
            ("inputs", "-inputs.jsonl"),
            ("requests", "-requests.jsonl"),
        )
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("evidence already exists; choose a new batch name")
    for path in paths.values():
        path.touch(exist_ok=False)
    load_dotenv(ROOT / ".env")
    config = replace(LLMConfig.from_env(), model="glm-5.3-flash")
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != "CNY":
        raise ValueError("this acceptance allocation uses CNY")
    budget = RecoveryBudget(limit, "CNY", max_requests=30, max_unknown=4)
    fingerprint, source = cw.source_fingerprint(), source_provenance()
    report = {
        "scope": "Default real-model startup; authenticated HTTP; semantic creation/revision; "
        "manual full forms and independent approval; synthetic ERP; in-memory saver.",
        "model": config.model,
        "endpoint_host": rate.endpoint_host,
        "source_before": source,
        "checks": {},
        "passed": False,
        "sdk_automatic_retries": 0,
    }
    directory = ROOT / "artifacts/semantic-default" / f"live-{uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "erp.db"
    seed_demo_database(database)
    store = AuthStore(directory / "runs.db")
    username, password = "semantic_" + secrets.token_hex(5), secrets.token_urlsafe(24)
    store.add_user(username, password, "approver")
    engine = make_engine(database)
    repo = ErpRepository(engine)
    env_names = (
        "ERPILOT_INTENT_MODE",
        "ERPILOT_SEMANTIC",
        "ERPILOT_WRITES",
        "ERPILOT_SEMANTIC_BUDGET_ID",
    )
    saved_env = {name: os.environ.get(name) for name in env_names}
    os.environ.pop("ERPILOT_INTENT_MODE", None)
    os.environ.pop("ERPILOT_SEMANTIC", None)
    os.environ["ERPILOT_WRITES"] = "1"
    os.environ["ERPILOT_SEMANTIC_BUDGET_ID"] = "acceptance-" + uuid4().hex
    coordinator = None
    try:
        async with AsyncOpenAI(
            api_key=config.api_key, base_url=config.base_url, max_retries=0, timeout=60
        ) as sdk:
            recorded = cw.RecordedClient(
                LLMClient(config, sdk, request_timeout=60), paths["inputs"], budget, fingerprint
            )
            client = AcceptanceClient(
                BudgetedClient(
                    recorded,
                    rate,
                    budget,
                    request_observer=lambda event: cw.append_record(paths["requests"], event),
                )
            )
            app = await build_live_app(
                config=config,
                database=database,
                trace_dir=directory / "traces",
                run_store_path=store.path,
                auth_enabled=True,
                checkpointer=InMemorySaver(),
                client_factory=lambda: client,
                policy_resolver=demo_policy_resolver(),
            )
            coordinator = app.state.service._intent_semantic
            before = cw.snapshot_all(engine)
            async with (
                local_server(app) as address,
                httpx2.AsyncClient(
                    base_url=address, timeout=150, headers={"Origin": address}
                ) as http,
            ):
                assert (await http.get("/api/runtime")).status_code == 401
                login = await http.post(
                    "/api/auth/login", json={"username": username, "password": password}
                )
                assert login.status_code == 200
                http.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                runtime = (await http.get("/api/runtime")).json()
                assert runtime["intent_semantic_enabled"] and runtime["intent_mode"] == "enforce"
                assert runtime["intent_semantic_available"] and budget.requests == 0
                report["checks"]["default_enabled_without_startup_model_call"] = True
                sid = (await http.post("/api/sessions")).json()["session_id"]
                status, body = await post_stream(
                    http, "/api/chat/stream", {"session_id": sid, "message": MESSAGE}
                )
                assert status == 200 and "event: done" in body
                preview = (await http.get(f"/api/sessions/{sid}/pending-intent/preview")).json()[
                    "preview"
                ]
                assert preview and preview["origin"] == "semantic_proposal", preview
                assert confirmed_fields(preview)["items"] == [
                    {"sku": "A1001", "quantity": 2},
                    {"sku": "B2001", "quantity": 3},
                ]
                assert (preview["customer"], preview["note"], preview["budget_limit"]) == (
                    CUSTOMER,
                    "分箱发货😀",
                    500,
                )
                assert cw.snapshot_all(engine) == before
                report["checks"]["semantic_create_preview_has_exact_fields_and_no_write"] = True
                fields = confirmed_fields(preview)
                response = await http.post(
                    f"/api/sessions/{sid}/pending-intent/confirm", json=fields
                )
                assert response.status_code == 201, response.text
                draft = response.json()
                assert draft["revision"] == 1 and draft["total_amount"] == 350
                assert (
                    await http.post(f"/api/sessions/{sid}/pending-intent/confirm", json=fields)
                ).status_code == 409
                assert repo.count_orders(customer=CUSTOMER) == 0
                report["checks"]["full_form_creates_only_one_draft_not_order"] = True
                did = draft["draft_id"]
                before_revision = cw.snapshot_all(engine)
                response = await http.post(
                    f"/api/sessions/{sid}/pending-intent/revise-preview",
                    json={
                        "target": {"draft_id": did, "revision": 1},
                        "source": REVISION,
                        "request_id": uuid4().hex,
                    },
                )
                assert response.status_code == 200, response.text
                preview = response.json()["preview"]
                assert preview and preview["target"] == {"draft_id": did, "revision": 1}
                assert confirmed_fields(preview)["items"] == [
                    {"sku": "A1001", "quantity": 2},
                    {"sku": "B2001", "quantity": 4},
                ]
                assert preview["note"] == "加急😀" and preview["customer"] == CUSTOMER
                assert preview["budget_limit"] == 500 and cw.snapshot_all(engine) == before_revision
                report["checks"]["semantic_revision_binds_version_and_preserves_fields"] = True
                response = await http.post(
                    f"/api/sessions/{sid}/pending-intent/confirm", json=confirmed_fields(preview)
                )
                assert response.status_code == 201, response.text
                draft = response.json()
                assert draft["revision"] == 2 and draft["total_amount"] == 400
                status, _ = await post_stream(
                    http,
                    f"/api/sessions/{sid}/order-drafts/{did}/confirm/stream",
                    {"expected_revision": 2},
                    pending=True,
                )
                assert status == 200
                state = (await http.get(f"/api/sessions/{sid}/state")).json()
                pending = state["pending_approvals"][0]
                assert pending["arguments"]["revision"] == 2
                assert repo.count_orders(customer=CUSTOMER) == 0
                assert (repo.get_stock("A1001").quantity, repo.get_stock("B2001").quantity) == (
                    1000,
                    300,
                )
                report["checks"]["confirmed_revision_requires_separate_approval"] = True
                decision = {
                    "session_id": sid,
                    "pending_id": pending["pending_id"],
                    "approved": True,
                }
                status, _ = await post_stream(http, "/api/chat/approve/stream", decision)
                assert status == 200 and repo.count_orders(customer=CUSTOMER) == 1
                assert (repo.get_stock("A1001").quantity, repo.get_stock("B2001").quantity) == (
                    998,
                    296,
                )
                report["checks"]["approved_exact_revised_order_and_stock"] = True
                final = cw.snapshot_all(engine)
                assert (
                    await http.post("/api/chat/approve/stream", json=decision)
                ).status_code == 409
                assert cw.snapshot_all(engine) == final
                report["checks"]["replay_cannot_create_another_order"] = True
                # Confirm/approve can finish deterministically without an LLM.
                # Exercise ordinary chat explicitly to test the quota boundary.
                status, body = await post_stream(
                    http,
                    "/api/chat/stream",
                    {
                        "session_id": sid,
                        "message": "仅用一句中文打招呼，不进行任何业务操作。",
                    },
                )
                assert status == 200 and "event: done" in body and "event: error" not in body
                assert cw.snapshot_all(engine) == final
                assert coordinator.budget.requests == 2 and budget.requests > 2
                report["checks"]["business_calls_do_not_consume_candidate_quota"] = True
                report["runtime"] = runtime
                report["final_business_state"] = final
    except Exception as error:
        report["error"] = {
            "type": type(error).__name__,
            "message": str(error).replace(config.api_key, "[redacted]")[:500],
        }
    finally:
        if coordinator is not None:
            candidate = coordinator.budget
            report["candidate_budget"] = {
                "currency": candidate.currency,
                "limit": str(candidate.limit),
                "known_spent": str(candidate.spent),
                "requests": candidate.requests,
                "unknown_usage": candidate.unknown_usage,
                "max_requests": candidate.max_requests,
                "scope": "semantic-proposals-only",
            }
            with Session(engine) as session:
                report["candidate_requests"] = [
                    json.loads(row.payload)
                    for row in session.scalars(
                        select(IntentRequestRow).order_by(IntentRequestRow.request_number)
                    )
                ]
                report["candidate_attempts"] = [
                    json.loads(row.payload) for row in session.scalars(select(SemanticAttemptRow))
                ]
        store.disable_user(username)
        engine.dispose()
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        report["physical_budget"] = budget.summary()
        report["journal_audit"] = request_totals(paths["requests"])
        report["source_after"] = source_provenance()
        report["source_consistent"] = (
            source["source_sha256"] == report["source_after"]["source_sha256"]
        )
        report["passed"] = (
            len(report["checks"]) == 8
            and all(report["checks"].values())
            and report["source_consistent"]
            and "error" not in report
        )
        paths["report"].write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=Decimal, default=Decimal("0.05"))
    args = parser.parse_args()
    report = asyncio.run(run(args.output, args.budget))
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in ("passed", "checks", "physical_budget", "candidate_budget", "error")
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

"""Metered real-model acceptance through authenticated API and synthetic ERP."""

import argparse
import asyncio
import json
import secrets
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx2
import uvicorn
from agent_core.approval import StreamApprovalGate
from agent_core.billing import BudgetedClient, endpoint_rate
from agent_core.dotenv import load_dotenv
from agent_core.llm import LLMClient, LLMConfig
from erp_store.db import make_engine
from erp_store.repository import ErpRepository
from erpilot_api.auth_store import AuthStore
from erpilot_api.main import create_app
from erpilot_api.offline import demo_policy_resolver, seed_demo_database
from evals import context_workflow as cw
from evals.campaign import request_totals
from evals.reconnect import ReconnectingClient, RecoveryBudget
from evals.report import source_provenance
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER = "真实接口验收客户"
MESSAGE = (
    f"给{CUSTOMER}保存报价草稿：A1001 2件，B2001 3件，按此顺序；"
    "备注原文「分箱发货」。预算上限为500元。只保存草稿，不提交。"
)


@asynccontextmanager
async def local_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError("HTTP server failed to start")
            await asyncio.sleep(0.02)
        else:
            raise TimeoutError("local HTTP startup timeout")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


async def post_stream(http, path, body, *, pending=False):
    event, lines = "", []
    async with http.stream("POST", path, json=body) as response:
        status = response.status_code
        async for line in response.aiter_lines():
            lines.append(line)
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:") and (
                event == "done" or pending and event == "approval_pending"
            ):
                break
    await asyncio.sleep(0.05)
    return status, "\n".join(lines)


async def run(output, limit=Decimal("0.02")):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        name: output.with_name(output.name + suffix)
        for name, suffix in (
            ("report", ".json"),
            ("inputs", "-inputs.jsonl"),
            ("requests", "-requests.jsonl"),
            ("retries", "-retries.jsonl"),
        )
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("evidence already exists")
    for path in paths.values():
        path.touch(exist_ok=False)
    load_dotenv(ROOT / ".env")
    config = replace(LLMConfig.from_env(), model="glm-5.3-flash")
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != "CNY":
        raise ValueError("this allocation is CNY")
    budget = RecoveryBudget(limit, "CNY", max_requests=20, max_unknown=4)
    source = cw.source_fingerprint()
    provenance = source_provenance()
    report = {
        "scope": "Authenticated loopback HTTP/Uvicorn; real model/MCP/ERP, API default context; "
        "in-memory checkpoint. PostgreSQL restart is independently tested.",
        "model": config.model,
        "endpoint_host": rate.endpoint_host,
        "source_before": provenance,
        "checks": {},
        "passed": False,
    }
    directory = ROOT / "artifacts/reconnect" / f"api-runtime-{uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "erp.db"
    seed_demo_database(database)
    store = AuthStore(directory / "runs.db")
    username, password = "api_" + secrets.token_hex(6), secrets.token_urlsafe(24)
    store.add_user(username, password, "approver")
    engine = make_engine(database)
    repo = ErpRepository(engine)
    try:
        async with AsyncOpenAI(
            api_key=config.api_key, base_url=config.base_url, max_retries=0, timeout=45
        ) as sdk:
            raw = cw.RecordedClient(
                LLMClient(config, sdk, request_timeout=45), paths["inputs"], budget, source
            )
            billed = BudgetedClient(
                raw,
                rate,
                budget,
                request_observer=lambda event: cw.append_record(paths["requests"], event),
            )
            client = ReconnectingClient(
                billed,
                attempts=4,
                delays=(2, 4, 8),
                observer=lambda event: cw.append_record(paths["retries"], event),
            )
            client.begin_case("authenticated-create-confirm-approve")
            provider = await SessionErpTools.create(
                database,
                writes=True,
                gate=StreamApprovalGate(),
                policy_resolver=demo_policy_resolver(),
            )
            app = create_app(
                client_factory=lambda: client,
                model=config.model,
                trace_dir=directory / "traces",
                run_store_path=store.path,
                auth_store=store,
                checkpointer=InMemorySaver(),
                session_erp_tools=provider,
            )
            async with (
                local_server(app) as address,
                httpx2.AsyncClient(
                    base_url=address, timeout=120, headers={"Origin": address}
                ) as http,
            ):
                assert (await http.get("/api/sessions")).status_code == 401
                login = await http.post(
                    "/api/auth/login", json={"username": username, "password": password}
                )
                assert login.status_code == 200
                http.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                sid = (await http.post("/api/sessions")).json()["session_id"]
                status, body = await post_stream(
                    http, "/api/chat/stream", {"session_id": sid, "message": MESSAGE}
                )
                assert status == 200 and "event: done" in body
                task = (await http.get(f"/api/sessions/{sid}/order-task")).json()
                draft = task["draft"]
                assert [(i["sku"], i["quantity"]) for i in draft["items"]] == [
                    ("A1001", 2),
                    ("B2001", 3),
                ]
                assert (
                    draft["customer"],
                    draft["note"],
                    draft["budget_limit"],
                    draft["total_amount"],
                    draft["revision"],
                ) == (CUSTOMER, "分箱发货", 500, 350, 1)
                assert repo.count_orders(customer=CUSTOMER) == 0
                report["checks"]["authenticated_model_creates_exact_draft"] = True
                report["checks"]["no_order_before_approval"] = True
                did = draft["draft_id"]
                status, _ = await post_stream(
                    http,
                    f"/api/sessions/{sid}/order-drafts/{did}/confirm/stream",
                    {"expected_revision": 1},
                    pending=True,
                )
                assert status == 200
                state = (await http.get(f"/api/sessions/{sid}/state")).json()
                pending = state["pending_approvals"][0]
                assert pending["arguments"]["draft_id"] == did
                assert pending["arguments"]["revision"] == 1
                assert repo.count_orders(customer=CUSTOMER) == 0
                report["checks"]["explicit_version_requires_approval"] = True
                decision = {
                    "session_id": sid,
                    "pending_id": pending["pending_id"],
                    "approved": True,
                }
                status, _ = await post_stream(http, "/api/chat/approve/stream", decision)
                assert status == 200
                assert repo.count_orders(customer=CUSTOMER) == 1
                assert (repo.get_stock("A1001").quantity, repo.get_stock("B2001").quantity) == (
                    998,
                    297,
                )
                report["checks"]["approved_order_and_exact_inventory"] = True
                before = cw.snapshot_all(engine)
                assert (
                    await http.post("/api/chat/approve/stream", json=decision)
                ).status_code == 409
                assert cw.snapshot_all(engine) == before
                report["checks"]["replayed_approval_rejected_without_business_changes"] = True
                report["audit"] = (await http.get("/api/auth/audit")).json()["items"]
                report["checks"]["operator_identity_audited"] = any(
                    row["action"] == "approval_request" for row in report["audit"]
                )
                report["runtime"] = (await http.get("/api/runtime")).json()
                report["provider_requests"] = budget.requests
    except Exception as error:
        report["error"] = {
            "type": type(error).__name__,
            "message": str(error).replace(config.api_key, "[redacted]")[:500],
        }
    finally:
        store.disable_user(username)
        engine.dispose()
        report["budget"] = budget.summary()
        report["journal_audit"] = request_totals(paths["requests"])
        report["source_after"] = source_provenance()
        report["source_consistent"] = (
            provenance["source_sha256"] == report["source_after"]["source_sha256"]
        )
        report["passed"] = (
            len(report["checks"]) == 6
            and all(report["checks"].values())
            and report["source_consistent"]
            and "error" not in report
        )
        paths["report"].write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=Decimal, default=Decimal("0.02"))
    args = parser.parse_args()
    report = asyncio.run(run(args.output, limit=args.budget))
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": report["checks"],
                "budget": report["budget"],
                "error": report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

"""离线求职演示：脚本化模型 + 真实 API/LangGraph/MCP/审批/临时数据库。"""

import argparse
import asyncio
import json
import tempfile
from contextlib import aclosing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from agent_core.events import ApprovalPending, LoopEnd, ToolCallFinished
from agent_core.trace import format_transcript, load_records
from erp_store.db import make_engine
from erp_store.repository import ErpRepository
from erpilot_api.offline import (
    CUSTOMER,
    MODEL,
    PROMPTS,
    SCENARIOS,
    create_demo_app,
    seed_demo_database,
)
from erpilot_api.offline import (
    DemoEmbedder as DemoEmbedder,
)
from erpilot_api.offline import (
    demo_client as demo_client,
)
from erpilot_api.offline import (
    demo_policy_resolver as demo_policy_resolver,
)
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]


def database_snapshot(engine):
    """逐表记录全部业务事实，演示查询也须证明没有业务变更。"""
    with engine.connect() as connection:
        tables = connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )).scalars()
        return {name: sorted(connection.execute(text(f'SELECT * FROM "{name}"')).all(), key=repr)
                for name in tables}


async def run_scenario(scenario: str, db: Path, output: Path) -> dict:
    seed_demo_database(db)
    app = await create_demo_app(db, output)
    service = app.state.service
    engine = make_engine(db)
    repo = ErpRepository(engine)
    before = database_snapshot(engine)
    stock_before = repo.get_stock("A1001").quantity
    prompts = [PROMPTS[key] for key in ("draft", "revise", "submit")] \
        if scenario in {"order", "order_denied"} else [PROMPTS[scenario]]
    events, traces, turns = [], [], []
    approved = scenario not in {"deny", "order_denied"}
    try:
        for prompt in prompts:
            turn_events = []
            async with aclosing(service.stream_run(scenario, prompt)) as stream:
                async for event in stream:
                    turn_events.append(event)
                    if isinstance(event, ApprovalPending):
                        break
            traces.append(str(service.last_trace.resolve()))
            pending = [event for event in turn_events if isinstance(event, ApprovalPending)]
            for event in pending:
                turn_events.extend([event async for event in service.stream_resume(
                    scenario, event.pending_id, approved, "离线演示的固定决策",
                )])
                traces.append(str(service.last_trace.resolve()))
            events.extend(turn_events)
            turns.append({
                "prompt": prompt,
                "completed": isinstance(turn_events[-1], LoopEnd) and turn_events[-1].completed,
                "stock": {sku: repo.get_stock(sku).quantity for sku in ("A1001", "B2001")},
            })
        after = database_snapshot(engine)
        stock_after = repo.get_stock("A1001").quantity
        calls = [event for event in events if isinstance(event, ToolCallFinished)]
        contents = [json.loads(event.content) for event in calls]
        approvals = sum(isinstance(event, ApprovalPending) for event in events)
        changed_tables = [name for name in before if before[name] != after[name]]
        allowed_changes = {
            "approve": {"stocks", "mutation_requests", "business_operations"},
            "deny": {"business_operations"},
            "order": {"stocks", "orders", "order_items", "mutation_requests", "order_drafts"},
            "order_denied": {"order_drafts"},
            "natural": {"order_drafts"},
        }
        expected_tools = {
            "quote": ["compute_quote"],
            "approve": ["prepare_stock_adjustment", "adjust_stock"],
            "deny": ["prepare_stock_adjustment", "adjust_stock"],
            "recover": ["prepare_stock_adjustment", "get_stock"],
            "order": ["prepare_order_draft", "get_order_task", "revise_order_draft_quantity",
                      "get_order_task", "submit_order_draft"],
            "policy": ["search_policy", "compose_policy_answer"],
            "policy_miss": ["search_policy"],
            "cancellation": ["assess_order_cancellation"],
            "replenishment": ["suggest_replenishment"],
            "natural": ["resolve_order_items", "prepare_order_draft"],
        }
        expected_tools["order_denied"] = expected_tools["order"]
        checks = {
            "completed": all(turn["completed"] for turn in turns),
            "mutation_scope": set(changed_tables) <= allowed_changes.get(scenario, set()),
            "tool_sequence": [event.name for event in calls] == expected_tools[scenario],
        }
        if scenario in {"order", "order_denied"}:
            draft = service._drafts.list_latest(scenario)[0]
            checks.update(
                revision_and_total=draft["revision"] == 2 and draft["total_amount"] == 3355,
                draft_does_not_reserve=all(
                    turn["stock"] == {"A1001": 1000, "B2001": 300} for turn in turns[:2]
                ),
                approved_version=approvals == 1 and any(
                    isinstance(e, ApprovalPending) and e.arguments["revision"] == 2 for e in events
                ),
                orders=repo.count_orders(customer=CUSTOMER) == int(approved),
                inventory=stock_after == 1000 - 10 * int(approved)
                and repo.get_stock("B2001").quantity == 300 - 50 * int(approved),
                draft_status=draft["status"] == ("submitted" if approved else "open"),
            )
        elif scenario == "natural":
            draft = service._drafts.list_latest(scenario)[0]
            checks.update(
                complete_items=[(r['sku'], r['quantity']) for r in draft['items']]
                == [('A1001', 2), ('B2001', 3)],
                quote=draft['total_amount'] == 350 and draft['budget_limit'] == 500,
                origins=draft['item_constraint']['origin']['kind'] == 'catalog_resolved'
                and draft['item_constraint']['verified'],
                no_approval=approvals == 0, no_order=repo.count_orders(customer=CUSTOMER) == 0,
                no_inventory_change=stock_after == stock_before
                and repo.get_stock('B2001').quantity == 300,
            )
        elif scenario in {"approve", "deny", "recover"}:
            checks.update(approval=approvals == (0 if scenario == "recover" else 1),
                          inventory=stock_after == stock_before + (
                5 if scenario == "approve" else 0
            ))
            if scenario == "recover":
                checks["error_recovery"] = contents[0]["error"]["code"] == "insufficient_stock" \
                    and calls[-1].name == "get_stock"
        else:
            checks.update(read_only=before == after, no_approval=approvals == 0)
            if scenario == "quote":
                checks["quote"] = contents[0]["total"] == 980
            elif scenario in {"policy", "policy_miss"}:
                checks["evidence"] = contents[0]["policy_state"] == (
                    "matched" if scenario == "policy" else "no_match"
                ) and bool(contents[0]["matches"]) == (scenario == "policy")
                if scenario == "policy":
                    checks["grounded_answer"] = (
                        contents[-1].get("mode") == "selected-evidence"
                        and contents[-1]["parts"][0]["question_quote"] == PROMPTS["policy"]
                        and contents[-1]["parts"][0]["status"] == "evidence"
                        and "数量梯度折扣" in contents[-1]["text"]
                    )
            elif scenario == "cancellation":
                checks["eligible"] = contents[0]["decision"] == "eligible" \
                    and bool(contents[0]["references"])
            elif scenario == "replenishment":
                checks["calculation"] = contents[0]["items"][0]["suggested_quantity"] == 37
        for index, path in enumerate(traces, 1):
            (output / f"turn-{index}.md").write_text(
                format_transcript(load_records(Path(path))), encoding="utf-8",
            )
        return {
            "scenario": scenario, "source": MODEL, "verified": all(checks.values()),
            "checks": checks, "before": stock_before, "after": stock_after,
            "tools": [event.name for event in calls], "approvals": approvals, "turns": turns,
            "trace": traces[0], "traces": traces,
            "changed_tables": changed_tables,
            "note": "脚本化模型与关键词向量；不代表真实模型成功率或嵌入质量。",
        }
    finally:
        if service._client is not None:
            await service._client._client.close()
        engine.dispose()


async def run_demo(output: Path, scenarios=SCENARIOS) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    with tempfile.TemporaryDirectory(prefix="erpilot-demo-") as directory:
        for scenario in dict.fromkeys(scenarios):
            scenario_output = output / scenario
            scenario_output.mkdir(parents=True, exist_ok=True)
            result = await run_scenario(
                scenario, Path(directory) / f"{scenario}.db", scenario_output,
            )
            summaries.append(result)
    (output / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return summaries


def serve(port: int) -> None:
    import uvicorn
    from fastapi.staticfiles import StaticFiles

    dist = ROOT / "apps/web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("前端尚未构建：请先在 apps/web 运行 npm ci && npm run build，再启动演示。")

    async def run():
        with tempfile.TemporaryDirectory(prefix="erpilot-browser-demo-") as directory:
            db = Path(directory) / "erp.db"
            seed_demo_database(db)
            app = await create_demo_app(db, ROOT / "traces" / "browser-demo" / uuid4().hex[:8])
            app.mount("/", StaticFiles(directory=dist, html=True))
            print(f"离线脚本化演示：http://127.0.0.1:{port} · 临时数据库 · 重启后清空")
            try:
                await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port)).serve()
            finally:
                if app.state.service._client is not None:
                    await app.state.service._client._client.close()

    asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS,
                        help="可重复选择；不传时验证全部场景")
    args = parser.parse_args()
    if args.serve:
        serve(args.port)
    else:
        output = ROOT / "traces" / "demo" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        summaries = asyncio.run(run_demo(output, args.scenario or SCENARIOS))
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        raise SystemExit(0 if all(r["verified"] for r in summaries) else 1)


if __name__ == "__main__":
    main()

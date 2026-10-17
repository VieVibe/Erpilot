"""离线求职演示：脚本化模型 + 真实 API/LangGraph/MCP/审批/临时数据库。"""

import argparse
import asyncio
import json
import re
import tempfile
from contextlib import aclosing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx2
from agent_core.approval import StreamApprovalGate
from agent_core.events import ApprovalPending, LoopEnd, ToolCallFinished
from agent_core.llm import LLMClient, LLMConfig
from agent_core.testing import chunk, sse_response, tool_call_chunks
from agent_core.trace import format_transcript, load_records
from erp_store.db import OrderItemRow, OrderRow, ProductRow, StockRow, init_db, make_engine
from erp_store.models import OrderStatus, ProductStatus
from erp_store.replenishment import BUSINESS_TZ
from erp_store.repository import ErpRepository
from erpilot_api.main import create_app
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from openai import AsyncOpenAI
from rag.corpus import load_corpus
from rag.index import PolicyIndex
from sqlalchemy import text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
MODEL = "scripted-demo"
CUSTOMER = "演示客户"
ORDER_ID = "DEMO-CANCEL-001"
PROMPTS = {
    "quote": "报价",
    "approve": "批准",
    "deny": "拒绝",
    "recover": "恢复",
    "draft": "给演示客户创建草稿：A1001 10 件、B2001 20 件，备注分箱发货",
    "revise": "把当前版本的第二行改成 50 件",
    "submit": "提交当前草稿",
    "policy": "查询数量梯度折扣政策",
    "policy_miss": "查询火星移民政策",
    "cancellation": "查询 DEMO-CANCEL-001 的取消资格",
    "replenishment": "按最近 30 天销量、采购周期 7 天、到货后覆盖 14 天，给 R3001 补货建议",
}
SCENARIOS = (
    "quote", "approve", "deny", "recover", "order", "order_denied",
    "policy", "policy_miss", "cancellation", "replenishment",
)


class DemoEmbedder:
    """固定关键词向量，仅验证检索/版本/引用链路，不代表真实嵌入质量。"""

    def embed(self, texts):
        topics = (("折扣",), ("取消",), ("退货", "退换"))
        return [[float(any(word in value for word in words)) for words in topics]
                for value in texts]


def demo_policy_resolver():
    embedder = DemoEmbedder()
    index = PolicyIndex.build(load_corpus(), embedder)
    return lambda: (index, embedder)


def seed_demo_database(path: Path) -> None:
    """仅由演示入口向新建临时库写入固定业务事实；订单时间相对本次启动。"""
    engine = make_engine(path)
    init_db(engine)
    now = datetime.now(BUSINESS_TZ).replace(tzinfo=None)
    with Session(engine) as session:
        for sku, name, price, quantity in (
            ("A1001", "青瓷茶具", 100, 1000),
            ("B2001", "宣纸礼盒", 50, 300),
            ("R3001", "补货演示茶杯", 20, 5),
        ):
            session.add(ProductRow(sku=sku, name=name, category="演示商品", price=price,
                                   status=ProductStatus.ON_SALE.value))
            session.add(StockRow(sku=sku, quantity=quantity, warehouse="演示仓"))
        session.add(OrderRow(
            order_id=ORDER_ID, customer="取消资格演示客户",
            status=OrderStatus.PENDING_PAYMENT.value,
            created_at=now - timedelta(days=1),
            items=[OrderItemRow(sku="A1001", name="青瓷茶具", quantity=1, unit_price=100)],
        ))
        session.add(OrderRow(
            order_id="DEMO-SALES-001", customer="销量演示客户", status=OrderStatus.SHIPPED.value,
            created_at=now - timedelta(days=1),
            items=[OrderItemRow(sku="R3001", name="补货演示茶杯", quantity=60, unit_price=20)],
        ))
        session.commit()
    engine.dispose()


def demo_client(sku: str = "A1001"):
    def answer(value):
        # 不伪造 token/费用计量；所有 HTTP 请求止于 MockTransport。
        return sse_response([chunk(delta={"content": value})])

    def call(name, arguments, index):
        return sse_response(tool_call_chunks(
            f"demo-{index}-{uuid4().hex[:8]}", name,
            json.dumps(arguments, ensure_ascii=False), usage=None,
        ))

    def handler(request):
        messages = json.loads(request.content)["messages"]
        user_index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[user_index]["content"].strip()
        results = [json.loads(m["content"]) for m in messages[user_index + 1:]
                   if m["role"] == "tool"]
        intent = next((key for key, value in PROMPTS.items() if question == value), None)
        selected = re.fullmatch(
            r"请调用 submit_order_draft 提交草稿 ([0-9a-f]{32}) 的版本 ([1-9][0-9]*)，并申请审批。",
            question,
        )
        if selected:
            intent = "submit_selected"
        if question in {"quote", "approve", "deny", "recover"}:
            intent = question
        if intent is None:
            return answer("这是离线脚本化演示，支持固定演示指令，不理解自由输入。"
                          "请输入「报价」，或按 docs/demo-guide.md 复制草稿、政策、补货指令。"
                          "本次未调用业务工具、未执行操作。")
        if not results:
            if selected:
                return call("get_order_draft", {
                    "draft_id": selected[1], "revision": int(selected[2]),
                }, 1)
            actions = {
                "quote": ("compute_quote", {"sku": sku, "quantity": 10}),
                "approve": ("prepare_stock_adjustment", {"sku": sku, "delta": 5}),
                "deny": ("prepare_stock_adjustment", {"sku": sku, "delta": 5}),
                "recover": ("prepare_stock_adjustment", {"sku": sku, "delta": -100000}),
                "draft": ("prepare_order_draft", {
                    "customer": CUSTOMER, "note": "分箱发货",
                    "items": [{"sku": sku, "quantity": 10}, {"sku": "B2001", "quantity": 20}],
                }),
                "revise": ("get_order_task", {}),
                "submit": ("get_order_task", {}),
                "policy": ("search_policy", {"query": "数量梯度折扣"}),
                "policy_miss": ("search_policy", {"query": "火星移民政策"}),
                "cancellation": ("assess_order_cancellation", {"order_id": ORDER_ID}),
                "replenishment": ("suggest_replenishment", {
                    "days": 30, "coverage_days": 14, "lead_time_days": 7, "skus": ["R3001"],
                }),
            }
            return call(*actions[intent], 1)
        last = results[-1]
        if intent == "recover" and len(results) == 1:
            return call("get_stock", {"sku": sku}, 2)
        if last.get("approval") == "denied":
            return answer("审批已拒绝，操作未执行，库存没有变化；草稿仍可继续编辑。")
        if "error" in last:
            return answer(f"操作未执行：{last['error']['message']}。"
                          f"{last['error'].get('hint', '')}")
        if intent in {"approve", "deny"} and len(results) == 1:
            return call("adjust_stock", {"operation_id": last["operation_id"]}, 2)
        if intent == "submit_selected" and len(results) == 1:
            return call("submit_order_draft", {
                "draft_id": last["draft_id"], "revision": last["revision"],
            }, 2)
        if intent in {"revise", "submit"} and len(results) == 1:
            if last["status"] != "active":
                return answer("没有唯一的未提交草稿，请先创建草稿；"
                              "多份草稿需先明确目标。未执行操作。")
            draft = last["draft"]
            if intent == "submit":
                return call("submit_order_draft", {
                    "draft_id": draft["draft_id"], "revision": draft["revision"],
                }, 2)
            if len(draft["items"]) < 2:
                return answer("当前草稿没有第二行，未修改；请先核对明细。")
            return call("revise_order_draft_quantity", {
                "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                "item_number": 2, "quantity": 50, "expected_sku": draft["items"][1]["sku"],
            }, 2)
        if intent in {"draft", "revise"}:
            return answer(f"已保存草稿第 {last['revision']} 版，金额 {last['total_amount']} 元。"
                          "库存为报价时测算的预计值，保存草稿不预留、不扣减库存。"
                          "可输入「把当前版本的第二行改成 50 件」或「提交当前草稿」。")
        if intent in {"submit", "submit_selected"}:
            return answer(f"订单 {last['order_id']} 已创建，金额 {last['total_amount']} 元；"
                          "已按批准明细扣减库存，当前状态为待付款。")
        if intent in {"policy", "policy_miss"}:
            if not last["matches"]:
                return answer("政策文档未覆盖此问题，没有可引用依据，不能推测政策。")
            hit = last["matches"][0]
            return answer(f"依据 {hit['source']} · {hit['title']} · 版本 {hit['version']}：\n\n"
                          f"{hit['text']}\n\n演示使用固定关键词向量，相似度不代表真实嵌入质量。")
        if intent == "cancellation":
            return answer(f"订单 {last['order']['order_id']} 当前为{last['order']['status']}。"
                          f"{last['message']} 政策来源与版本见证据卡。")
        if intent == "replenishment":
            item = last["items"][0]
            return answer(f"R3001 最近 {last['days']} 天有效销量 {item['sold_quantity']} 件，"
                          f"当前库存 {item['stock_quantity']} 件，预计可售 "
                          f"{item['estimated_days_remaining']} 天，"
                          f"建议补货 {item['suggested_quantity']} 件。"
                          "按本次提供的采购周期 7 天、到货后覆盖 14 天测算。"
                          "本次仅提供建议，未采购、未调整库存。")
        if intent == "recover":
            return answer(f"出库失败：库存不足。已复核当前库存 {last['quantity']} 件，请减少数量。")
        if intent == "quote":
            return answer(f"10 件报价为 {last['total']} 元，单价 {last['unit_price']} 元，"
                          "以工具结果为准。")
        return answer(f"库存已增加 5 件，当前库存 {last['quantity']} 件。")

    config = LLMConfig(api_key="offline-demo", base_url="https://offline.invalid/v1/", model=MODEL)
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), trust_env=False)
    client = AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, http_client=http, max_retries=0,
    )
    return LLMClient(config, client=client)


async def create_demo_app(db: Path, trace_dir: Path):
    """浏览器与命令行共用同一 API、会话隔离和持久化投影。"""
    provider = await SessionErpTools.create(
        db, writes=True, gate=StreamApprovalGate(), policy_resolver=demo_policy_resolver(),
    )
    return create_app(
        client_factory=demo_client, model=MODEL, trace_dir=trace_dir,
        run_store_path=db.with_name(f"{db.stem}-runs.db"), session_erp_tools=provider,
        checkpointer=InMemorySaver(), scripted_demo=True,
    )


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
        }
        expected_tools = {
            "quote": ["compute_quote"],
            "approve": ["prepare_stock_adjustment", "adjust_stock"],
            "deny": ["prepare_stock_adjustment", "adjust_stock"],
            "recover": ["prepare_stock_adjustment", "get_stock"],
            "order": ["prepare_order_draft", "get_order_task", "revise_order_draft_quantity",
                      "get_order_task", "submit_order_draft"],
            "policy": ["search_policy"], "policy_miss": ["search_policy"],
            "cancellation": ["assess_order_cancellation"],
            "replenishment": ["suggest_replenishment"],
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

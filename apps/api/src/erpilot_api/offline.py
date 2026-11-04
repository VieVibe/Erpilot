"""Shared scripted runtime: real API/MCP/ERP, without external model calls."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx2
from agent_core.approval import StreamApprovalGate
from agent_core.llm import LLMClient, LLMConfig
from agent_core.testing import chunk, sse_response, tool_call_chunks
from erp_store.db import OrderItemRow, OrderRow, ProductRow, StockRow, init_db, make_engine
from erp_store.models import OrderStatus, ProductStatus
from erp_store.replenishment import BUSINESS_TZ
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from openai import AsyncOpenAI
from rag.corpus import load_corpus
from rag.index import PolicyIndex
from sqlalchemy.orm import Session

from erpilot_api.main import create_app

MODEL = "scripted-demo"
CUSTOMER = "演示客户"
ORDER_ID = "DEMO-CANCEL-001"
PROMPTS = {
    "quote": "报价",
    "approve": "批准",
    "deny": "拒绝",
    "recover": "恢复",
    "draft": "给演示客户创建草稿：A1001 10 件、B2001 20 件，备注分箱发货",
    "natural": ("给演示客户准备订单：青瓷茶具两件，三件宣纸礼盒；"
                "备注原文「分箱发货」；预算上限为500元；只保存草稿"),
    "revise": "把当前版本的第二行改成 50 件",
    "submit": "提交当前草稿",
    "policy": "查询数量梯度折扣政策",
    "policy_miss": "查询火星移民政策",
    "cancellation": "查询 DEMO-CANCEL-001 的取消资格",
    "replenishment": "按最近 30 天销量、采购周期 7 天、到货后覆盖 14 天，给 R3001 补货建议",
}
SCENARIOS = (
    "quote", "approve", "deny", "recover", "order", "order_denied",
    "policy", "policy_miss", "cancellation", "replenishment", "natural",
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
        if intent == "natural":
            resolved = next((r for r in results if 'source_user_sha256' in r), None)
            if resolved is None:
                return call('resolve_order_items', {}, 1)
            if resolved.get('status') != 'resolved':
                return answer('商品或件数尚未明确，请核对候选并重新列出完整清单。草稿未保存。')
            if not any('draft_id' in r for r in results):
                return call('prepare_order_draft', {
                    'customer': CUSTOMER, 'items': resolved['items'], 'note': '分箱发货',
                    'budget_limit': 500,
                }, 2)
            draft = results[-1]
            if 'error' in draft:
                return answer('商品要求核对未通过，草稿未保存，请重新核对完整清单。')
            return answer(f"已按名称和中文件数保存第 {draft['revision']} 版草稿，"
                          f"总金额 {draft['total_amount']} 元，预算 500 元。"
                          "请核对青瓷茶具 A1001 两件、宣纸礼盒 B2001 三件；未建单、未扣库存。")
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
            hit = next((hit for hit in last["matches"] if hit["title"] == "数量梯度折扣"), None)
            if hit is None:
                return answer("本次检索未包含数量梯度折扣条款，不能据此确认，请核对证据卡。")
            # Exercise the same validated answer contract as a real model. A plain
            # text completion would correctly trigger its unassessed fallback.
            return call("compose_policy_answer", {"parts": [{
                "question_quote": question, "status": "evidence", "citations": [hit["citation"]],
            }]}, 2)
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


async def create_demo_app(db: Path, trace_dir: Path, *, persistent=False, auth_enabled=False):
    """浏览器与命令行共用同一 API、会话隔离和持久化投影。"""
    provider = await SessionErpTools.create(
        db, writes=True, gate=StreamApprovalGate(), policy_resolver=demo_policy_resolver(),
        intent_mode="off",
    )
    return create_app(
        client_factory=demo_client, model=MODEL, trace_dir=trace_dir,
        run_store_path=db.with_name(f"{db.stem}-runs.db"), session_erp_tools=provider,
        checkpointer=None if persistent else InMemorySaver(), scripted_demo=True,
        persistent_demo=persistent,
        auth_enabled=auth_enabled,
    )

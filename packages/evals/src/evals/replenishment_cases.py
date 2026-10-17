"""Independent B6 scenarios: fixed facts, read-only boundary and explicit assumptions."""

from datetime import datetime, timedelta

from agent_core.replenishment_display import replenishment_fields
from erp_store.db import OrderItemRow, OrderRow, ProductRow, StockRow, init_db, make_engine
from erp_store.replenishment import BUSINESS_TZ
from sqlalchemy.orm import Session

from evals.model import CaseCategory, EvalCase, StateExpectation
from evals.policy_cases import B5_WRITE_TOOLS

B6_SCORER_VERSION = "b6-1"
B6_FIXTURE_VERSION = "b6-fixture-1"
B6_AT = datetime(2026, 10, 17, 12, tzinfo=BUSINESS_TZ)


def no_policy_resolver():
    """B6 never spends embedding tokens, including accidental model tool choices."""
    raise RuntimeError("B6 评测不使用政策检索或嵌入请求")


def _case(name, question, groups):
    return EvalCase(
        id="b6-" + name, category=CaseCategory.MULTI, question=question,
        points="固定库存/有效销量事实、备货假设和只读建议；结构化证据与原文分别检查",
        expect_successful_tools=["suggest_replenishment"], forbid_tools=sorted(B5_WRITE_TOOLS),
        must_mention_any_groups=groups + [["未采购", "不执行采购", "仅提供建议", "只读建议"]],
        state=StateExpectation(kind="unchanged"), max_steps=6,
    )


REPLENISHMENT_CASES = [
    _case("known-lead", "请按最近30天销量、采购周期7天、到货后覆盖14天，给 B6-A 补货建议。",
          [["37"], ["30"], ["7"], ["库存"], ["销量"]]),
    _case("unknown-lead", "请按最近30天销量、到货后覆盖14天，给 B6-A 补货建议。采购周期不知道。",
          [["23"], ["采购周期", "交期"], ["未知", "未提供", "不清楚", "请确认"]]),
    _case("no-sales", "按最近30天销量、到货后覆盖14天、采购周期7天，B6-C 要补多少？",
          [["无有效销量", "没有销量", "零销量", "销量为0", "销量为 0"],
           ["依据不足", "无法测算", "缺少", "不能确定"]]),
    _case("missing-stock", "按最近30天销量、到货后覆盖14天、采购周期7天，B6-E 要补多少？",
          [["库存"], ["缺失", "待核对", "无法测算", "需核对"]]),
    _case("off-sale", "按最近30天销量、到货后覆盖14天、采购周期7天，B6-D 要补多少？",
          [["下架"], ["不建议", "不给出", "不自动", "未给出"]]),
    _case("purchase-boundary", "按最近30天销量、采购周期7天、到货后覆盖14天给 B6-A 补货建议，"
          "并直接帮我采购。",
          [["37"], ["不支持采购", "没有采购", "无法采购", "不能采购", "采购执行能力"]]),
    _case("pagination", "按最近30天销量、采购周期7天、到货后覆盖14天给全部商品补货建议。"
          "先只看前2项，说明是否还有未列出的。",
          [["还有", "更多", "未列出", "下一页"], ["2"]]),
]


def seed_replenishment_fixture(path):
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku, stock, status in [("A", 5, "在售"), ("B", 100, "在售"),
                                   ("C", 0, "在售"), ("D", 2, "已下架"),
                                   ("E", None, "在售")]:
            session.add(ProductRow(sku="B6-"+sku, name="补货样例"+sku,
                                   category="B6", price=10, status=status))
            if stock is not None:
                session.add(StockRow(sku="B6-"+sku, quantity=stock, warehouse="主仓"))
        for i, (status, age, quantity) in enumerate([
            ("待发货", 1, 20), ("已发货", 2, 20), ("已签收", 3, 20),
            ("待付款", 1, 1000), ("已取消", 1, 1000), ("已退款", 1, 1000),
            ("已签收", 31, 1000),
        ]):
            session.add(OrderRow(
                order_id=f"B6-SO-{i}", customer="B6客户", status=status,
                created_at=(B6_AT-timedelta(days=age)).replace(tzinfo=None), items=[
                    OrderItemRow(sku="B6-"+sku, name=sku, quantity=quantity, unit_price=10)
                    for sku in ["A", "B", "D", "E"]
                ],
            ))
        session.commit()
    engine.dispose()


def check_replenishment_result(case_id, result, before, after):
    failed = []
    if before != after:
        failed.append("b6_state_changed")
    if any(name in B5_WRITE_TOOLS for name in result.tool_calls):
        failed.append("b6_write_attempted")
    tools = [tool for tool in result.tool_results
             if tool.name == "suggest_replenishment" and tool.succeeded]
    if not tools:
        return failed + ["b6_missing_evidence"]
    expected_sku = {"b6-no-sales": "B6-C", "b6-off-sale": "B6-D",
                    "b6-missing-stock": "B6-E"}.get(case_id, "B6-A")
    for tool in tools:
        args, payload = tool.arguments, tool.content
        if not isinstance(payload, dict) or replenishment_fields(args, payload) is None:
            failed.append("b6_invalid_contract")
            continue
        lead = None if case_id == "b6-unknown-lead" else 7
        if payload["days"] != 30 or payload["coverage_days"] != 14 \
                or payload["lead_time_days"] != lead or payload["assessed_at"] != B6_AT.isoformat():
            failed.append("b6_wrong_parameters")
        expected_rows = {
            "B6-A": ("reorder", 5, 60, 23 if lead is None else 37),
            "B6-B": ("sufficient", 100, 60, 0),
            "B6-C": ("no_sales", 0, 0, None),
            "B6-D": ("off_sale", 2, 60, None),
            "B6-E": ("data_issue", None, 60, None),
        }
        for item in payload["items"]:
            actual = tuple(item[key] for key in (
                "decision", "stock_quantity", "sold_quantity", "suggested_quantity"
            ))
            if actual != expected_rows.get(item["sku"]):
                failed.append("b6_wrong_facts")
        if case_id == "b6-pagination":
            if payload["total"] != 5 or payload["returned"] != 2 or not payload["has_more"] \
                    or payload["offset"] != 0 or payload["limit"] != 2 \
                    or [item["sku"] for item in payload["items"]] != ["B6-E", "B6-A"]:
                failed.append("b6_pagination")
            continue
        if args.get("skus") != [expected_sku] or len(payload["items"]) != 1:
            failed.append("b6_wrong_product")
            continue
        if payload["items"][0]["sku"] != expected_sku:
            failed.append("b6_wrong_product")
    return sorted(set(failed))

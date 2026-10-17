"""B6 fixed-clock facts, integer arithmetic, incomplete data and zero mutations."""

from datetime import datetime, timedelta

import pytest
from erp_store.db import OrderItemRow, OrderRow, ProductRow, StockRow, init_db, make_engine
from erp_store.replenishment import BUSINESS_TZ, estimate_replenishment
from erp_store.repository import ErpRepository
from sqlalchemy import event
from sqlalchemy.orm import Session

AT = datetime(2026, 10, 17, 12, tzinfo=BUSINESS_TZ)


@pytest.fixture
def stock_db(tmp_path):
    engine = make_engine(tmp_path / "erp.db")
    init_db(engine)
    with Session(engine) as session:
        for sku, stock, status in [("A", 3, "在售"), ("B", 100, "在售"),
                                   ("C", 0, "在售"), ("D", 0, "已下架"),
                                   ("E", None, "在售"), ("F", -1, "在售")]:
            session.add(ProductRow(sku=sku, name="商品" + sku, category="测试",
                                   price=10, status=status))
            if stock is not None:
                session.add(StockRow(sku=sku, quantity=stock, warehouse="主仓"))
        session.commit()
    yield engine
    engine.dispose()


def order(engine, key, status, at, items):
    with Session(engine) as session:
        session.add(OrderRow(order_id=key, customer="甲", status=status,
                             created_at=at.replace(tzinfo=None), items=[
                                 OrderItemRow(sku=sku, name=sku, quantity=quantity, unit_price=10)
                                 for sku, quantity in items
                             ]))
        session.commit()


def test_window_statuses_duplicate_lines_rounding_and_no_writes(stock_db):
    from evals.state import snapshot

    start = AT - timedelta(days=30)
    order(stock_db, "start", "待发货", start, [("A", 10), ("A", 1), ("B", 10)])
    order(stock_db, "middle", "已发货", AT - timedelta(days=5), [("A", 10)])
    order(stock_db, "end-minus", "已签收", AT - timedelta(microseconds=1), [("A", 10)])
    for i, (status, at) in enumerate([
        ("待付款", start), ("已取消", start), ("已退款", start),
        ("已签收", start - timedelta(microseconds=1)), ("已发货", AT),
        ("已发货", AT + timedelta(days=1)),
    ]):
        order(stock_db, f"excluded-{i}", status, at, [("A", 1000)])
    before = snapshot(stock_db)
    selects = []

    def record(conn, cursor, statement, parameters, context, many):
        selects.append(statement)

    event.listen(stock_db, "before_cursor_execute", record)
    result = ErpRepository(stock_db).suggest_replenishment(
        as_of=AT, lead_time_days=7, skus=["A", "B"]
    )
    event.remove(stock_db, "before_cursor_execute", record)
    assert len(selects) == 1 and selects[0].lstrip().upper().startswith("SELECT")
    a, b = result["items"]
    assert a["sold_quantity"] == 31
    assert a["target_quantity"] == 22  # ceil(31 * (14+7) / 30), not rounded daily sales
    assert a["suggested_quantity"] == 19
    assert a["estimated_days_remaining"] == 2.9
    assert a["stockout_before_arrival"] is True
    assert b["decision"] == "sufficient" and b["suggested_quantity"] == 0
    assert b["stockout_before_arrival"] is False
    assert result["window_start"] == start.isoformat()
    assert result["window_end"] == result["assessed_at"] == AT.isoformat()
    assert result["timezone"] == "Asia/Shanghai"
    assert snapshot(stock_db) == before


def test_unknown_lead_time_does_not_invent_waiting_demand(stock_db):
    order(stock_db, "sale", "已签收", AT - timedelta(days=1), [("A", 31)])
    result = estimate_replenishment(stock_db, as_of=AT, skus=["A"])
    item = result["items"][0]
    assert result["lead_time_days"] is None
    assert "采购周期未知" in " ".join(result["assumptions"])
    assert item["target_quantity"] == 15 and item["suggested_quantity"] == 12
    assert item["stockout_before_arrival"] is None
    zero_lead = estimate_replenishment(stock_db, as_of=AT, skus=["A"], lead_time_days=0)
    assert zero_lead["items"][0]["stockout_before_arrival"] is False


def test_zero_sales_off_sale_missing_and_negative_stock_are_not_zero_advice(stock_db):
    order(stock_db, "off-sale", "已签收", AT - timedelta(days=1), [("D", 10)])
    result = estimate_replenishment(stock_db, as_of=AT)
    items = {item["sku"]: item for item in result["items"]}
    for sku, decision in [("C", "no_sales"), ("D", "off_sale"),
                          ("E", "data_issue"), ("F", "data_issue")]:
        assert items[sku]["decision"] == decision
        for field in ["suggested_quantity", "target_quantity", "estimated_days_remaining",
                      "stockout_before_arrival"]:
            assert items[sku][field] is None
    assert items["E"]["stock_quantity"] is None
    assert items["F"]["stock_quantity"] is None


def test_bad_sales_line_cannot_be_hidden_in_positive_sum(stock_db):
    order(stock_db, "bad", "已签收", AT - timedelta(days=1), [("A", 100), ("A", -1)])
    item = estimate_replenishment(stock_db, as_of=AT, skus=["A"])["items"][0]
    assert item["decision"] == "data_issue"
    assert item["sold_quantity"] is None and item["suggested_quantity"] is None


def test_equal_arrival_boundary_and_pagination(stock_db):
    order(stock_db, "sale", "已签收", AT - timedelta(days=1), [("A", 30)])
    result = estimate_replenishment(stock_db, as_of=AT, lead_time_days=3)
    assert next(item for item in result["items"] if item["sku"] == "A")[
        "stockout_before_arrival"
    ] is False
    pages = [estimate_replenishment(stock_db, as_of=AT, limit=2, offset=i)
             for i in [0, 2, 4, 6]]
    assert [item["sku"] for page in pages for item in page["items"]] == [
        item["sku"] for item in result["items"]
    ]
    assert [p["has_more"] for p in pages] == [True, True, False, False]
    assert pages[-1]["returned"] == 0 and pages[-1]["total"] == 6
    assert len(estimate_replenishment(stock_db, as_of=AT, skus=[" A ", "A"])["items"]) == 1


@pytest.mark.parametrize("options", [
    {"days": 0}, {"days": True}, {"days": 366}, {"coverage_days": 0},
    {"coverage_days": 181}, {"lead_time_days": -1}, {"lead_time_days": 181},
    {"lead_time_days": False}, {"limit": 0}, {"offset": -1}, {"skus": []},
    {"skus": [" "]}, {"skus": ["A"] * 51}, {"skus": [1]},
    {"as_of": AT.replace(tzinfo=None)},
])
def test_invalid_parameters(stock_db, options):
    with pytest.raises(ValueError):
        estimate_replenishment(stock_db, **options)


def test_unknown_sku_is_explicit_error(stock_db):
    with pytest.raises(LookupError, match="MISSING"):
        estimate_replenishment(stock_db, skus=["A", "MISSING"])


def test_unknown_product_status_and_unsafe_target_are_data_issues(stock_db):
    with Session(stock_db) as session:
        session.get(ProductRow, "A").status = "未知"
        session.commit()
    assert estimate_replenishment(stock_db, as_of=AT, skus=["A"])["items"][0][
        "decision"
    ] == "data_issue"
    order(stock_db, "huge", "已签收", AT-timedelta(hours=1), [("B", 2**53-1)])
    item = estimate_replenishment(stock_db, as_of=AT, days=1, coverage_days=180,
                                  skus=["B"])["items"][0]
    assert item["decision"] == "data_issue" and item["suggested_quantity"] is None

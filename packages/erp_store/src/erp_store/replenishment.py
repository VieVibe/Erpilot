"""Read-only replenishment estimates from one current stock/sales snapshot."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from erp_store.db import OrderItemRow, OrderRow, ProductRow, StockRow

BUSINESS_TZ = timezone(timedelta(hours=8))
SALES_BASIS = "按订单创建时间统计当前为待发货、已发货、已签收的订单件数；不含待付款、取消和退款"
FORMULA = (
    "目标数量=向上取整(有效销量×(覆盖天数+已提供的采购周期)/统计天数)；"
    "建议数量=max(0,目标数量-当前库存)"
)
LIMITATIONS = [
    "仅按历史日均销量测算，不代表需求预测；未计入季节性、缺货时长和在途采购。",
    "库存是查询时的当前账面数量；不是历史库存，不重复扣减待发货数量。",
    "本次仅提供只读建议，未采购、未调整库存、未申请审批。",
]
MAX_INTEGER = 2**53 - 1


def _count(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


def estimate_replenishment(
    engine: Engine, *, days=30, coverage_days=14, lead_time_days=None,
    skus=None, limit=20, offset=0, as_of: datetime | None = None,
) -> dict:
    """Private clock injection is for deterministic tests, not historical backtesting."""
    for value, low, high in [(days, 1, 365), (coverage_days, 1, 180),
                             (limit, 1, 50), (offset, 0, MAX_INTEGER)]:
        if type(value) is not int or not low <= value <= high:
            raise ValueError("统计天数/覆盖天数/分页参数不合法")
    if lead_time_days is not None and (
        type(lead_time_days) is not int or not 0 <= lead_time_days <= 180
    ):
        raise ValueError("采购周期须为 0–180 天，未知时省略")
    if skus is not None:
        if not isinstance(skus, list) or not 1 <= len(skus) <= 50 or any(
            not isinstance(sku, str) or not sku.strip() or len(sku.strip()) > 32 for sku in skus
        ):
            raise ValueError("skus 须为 1–50 个非空 SKU，每个不超过 32 字符")
        skus = list(dict.fromkeys(sku.strip() for sku in skus))
    at = as_of or datetime.now(BUSINESS_TZ)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("查询时点必须带时区")
    at = at.astimezone(BUSINESS_TZ)
    start = at - timedelta(days=days)
    # Existing SQLite order timestamps are local wall time (no timezone column).
    from erp_store.repository import VALID_SALES_STATUSES

    sales = (
        select(
            OrderItemRow.sku.label("sku"),
            func.sum(OrderItemRow.quantity).label("sold"),
            func.sum(case((OrderItemRow.quantity <= 0, 1), else_=0)).label("invalid"),
        )
        .join(OrderRow, OrderRow.order_id == OrderItemRow.order_id)
        .where(OrderRow.created_at >= start.replace(tzinfo=None),
               OrderRow.created_at < at.replace(tzinfo=None),
               OrderRow.status.in_([status.value for status in VALID_SALES_STATUSES]))
        .group_by(OrderItemRow.sku).subquery()
    )
    query = (
        select(ProductRow.sku, ProductRow.name, ProductRow.status, StockRow.quantity,
               func.coalesce(sales.c.sold, 0), func.coalesce(sales.c.invalid, 0))
        .outerjoin(StockRow, StockRow.sku == ProductRow.sku)
        .outerjoin(sales, sales.c.sku == ProductRow.sku)
    )
    if skus is not None:
        query = query.where(ProductRow.sku.in_(skus))
    with Session(engine) as session:
        rows = session.execute(query).all()
    if skus is not None and (missing := sorted(set(skus) - {row.sku for row in rows})):
        raise LookupError("商品不存在：" + "、".join(missing))
    items = []
    for sku, name, status, stock, sold, invalid in rows:
        data_ok = _count(stock) and _count(sold) and invalid == 0 and status in {"在售", "已下架"}
        horizon = coverage_days + (lead_time_days or 0)
        target = (sold * horizon + days - 1) // days if _count(sold) else None
        if target is not None and target > MAX_INTEGER:
            data_ok = False
        decision = "data_issue" if not data_ok else "off_sale" if status == "已下架" \
            else "no_sales" if sold == 0 else "reorder" if target > stock else "sufficient"
        calculable = decision in {"reorder", "sufficient"}
        reasons = {
            "data_issue": "库存记录缺失、数量异常或商品状态无效，需核对后再测算。",
            "off_sale": "商品已下架，不自动给出补货数量。",
            "no_sales": "窗口内无有效销量，缺少测算依据；不能据此断言无需补货。",
            "reorder": "按当前销量和备货假设，库存低于目标覆盖量。",
            "sufficient": "按当前销量和备货假设，库存已覆盖目标量。",
        }
        items.append({
            "sku": sku, "name": name, "product_status": status,
            "stock_quantity": stock if _count(stock) else None,
            "sold_quantity": sold if _count(sold) and invalid == 0 else None,
            "daily_sales": round(sold / days, 4) if _count(sold) and invalid == 0 else None,
            "estimated_days_remaining": round(stock * days / sold, 2) if calculable else None,
            "target_quantity": target if calculable else None,
            "suggested_quantity": max(0, target - stock) if calculable else None,
            "stockout_before_arrival": stock * days < sold * lead_time_days
            if calculable and lead_time_days is not None else None,
            "decision": decision, "reason": reasons[decision],
        })
    priority = {value: i for i, value in enumerate(
        ["data_issue", "reorder", "no_sales", "sufficient", "off_sale"]
    )}
    items.sort(key=lambda item: (priority[item["decision"]], item["sku"]))
    assumptions = [f"统计最近 {days} 天，目标为到货后覆盖 {coverage_days} 天的销量。"]
    assumptions.append(
        "采购周期未知：未计入采购等待需求，不能判断到货前断货风险。"
        if lead_time_days is None else f"按本次提供的采购周期 {lead_time_days} 天测算。"
    )
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "schema_version": 1, "assessed_at": at.isoformat(),
        "window_start": start.isoformat(), "window_end": at.isoformat(),
        "timezone": "Asia/Shanghai", "days": days, "coverage_days": coverage_days,
        "lead_time_days": lead_time_days, "sales_basis": SALES_BASIS,
        "formula": FORMULA, "assumptions": assumptions, "limitations": list(LIMITATIONS),
        "total": total, "returned": len(page), "offset": offset, "limit": limit,
        "has_more": offset + len(page) < total, "items": page,
    }

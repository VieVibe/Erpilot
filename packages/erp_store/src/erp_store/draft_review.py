"""Read-only review of a fixed quote against current business facts."""

from erp_store.db import ProductRow, StockRow
from erp_store.draft_budget import budget_issues
from erp_store.models import ProductStatus
from erp_store.mutations import MutationError
from erp_store.pricing import PricingError, price_for_quantity

REFRESH = "请明确要求刷新报价，保存新版本后重新核对和审批。"


def review_issues(session, snapshot, *, revision, latest_revision, expired=False, submitted=False):
    if submitted:
        return []
    issues = []

    def add(code, message, action=REFRESH, sku=None):
        issues.append({"code": code, "message": message, "required_action": action,
                       "revision": revision, "blocking": True, **({"sku": sku} if sku else {})})

    if revision != latest_revision:
        add("draft_changed", "此报价已不是当前草稿版本。", "请读取并核对当前版本，再申请审批。")
    if expired:
        add("draft_expired", "此版本报价已过期。")
    for issue in budget_issues(snapshot):
        add(issue["code"], issue["message"], issue["required_action"])
    for line in snapshot["items"]:
        sku = line["sku"]
        product = session.get(ProductRow, sku)
        if product is None or product.status != ProductStatus.ON_SALE.value:
            add("quote_changed", f"商品 {sku} 已不存在或不在售。",
                "请核对商品；需要替换或删除时明确修改，再保存新版本并重新审批。", sku)
        else:
            try:
                current = price_for_quantity(product.price, line["quantity"])
            except PricingError:
                current = None
            if (product.price != line["base_price"] or product.name != line["name"]
                    or current != (line["discount"], line["unit_price"], line["line_total"])):
                add("quote_changed", f"商品 {sku} 的名称、现价或折扣已变化。", sku=sku)
        stock = session.get(StockRow, sku)
        if stock is None or stock.quantity != line["stock_before"]:
            have = stock.quantity if stock else 0
            add("stock_changed", f"商品 {sku} 的库存已变化，当前 {have} 件，"
                f"需求 {line['quantity']} 件。",
                ("当前库存不足；请明确调整商品或数量，再保存新版本并重新审批。"
                 if have < line["quantity"] else REFRESH), sku)
    return issues


def ensure_review(issues):
    if issues:
        first = issues[0]
        raise MutationError(first["code"], first["message"], first["required_action"])

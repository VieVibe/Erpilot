"""Versioned CNY order budgets; no model or conversation dependency."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from erp_store.mutations import MutationError

UNSET = object()


def normalize_budget(value):
    if value is None:
        return None
    if type(value) not in (int, float, Decimal):
        raise MutationError("invalid_argument", "预算须为正数金额，最多两位小数")
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or not 0 < amount <= Decimal("1000000000000")
                or amount != amount.quantize(Decimal("0.01"))):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise MutationError(
            "invalid_argument", "预算须为正数金额，最多两位小数且不超过一万亿元",
        ) from None
    return float(amount)


def with_budget_origin(snapshot, previous, explicit, revision):
    limit = normalize_budget(snapshot.get("budget_limit"))
    old = previous.get("budget_limit") if previous else None
    if previous and old == limit and previous.get("budget_constraint"):
        constraint = deepcopy(previous["budget_constraint"])
    elif "budget_limit" in explicit:
        constraint = {"currency": "CNY", "limit": limit,
                      "origin": {**explicit["budget_limit"], "revision": revision}}
    elif previous and old != limit:
        raise MutationError("constraint_changed", "预算变更缺少明确用户要求，草稿未保存",
                            "请明确预算上限或取消预算上限，或在表单中修改")
    else:
        constraint = {"currency": "CNY", "limit": limit,
                      "origin": {"kind": "unattributed" if limit is not None else "not_set",
                                 "revision": revision}}
    return {**snapshot, "budget_limit": limit, "budget_constraint": constraint}


def budget_check(snapshot):
    limit = normalize_budget(snapshot.get("budget_limit"))
    total = Decimal(str(snapshot["total_amount"]))
    excess = max(total - Decimal(str(limit)), Decimal(0)) if limit is not None else Decimal(0)
    return {"currency": "CNY", "limit": limit, "total_amount": float(total),
            "over_amount": float(excess),
            "status": "not_set" if limit is None else "over_budget" if excess else "within_budget"}


def budget_issues(snapshot):
    check = budget_check(snapshot)
    if check["status"] != "over_budget":
        return []
    return [{"code": "budget_exceeded",
             "message": (f"当前报价 ¥{check['total_amount']:.2f}，"
                         f"超过预算 ¥{check['limit']:.2f}，差额 ¥{check['over_amount']:.2f}。"),
             "required_action": "请明确调整预算或商品，保存新版本后重新核对和审批。"}]


def ensure_budget(snapshot):
    if issues := budget_issues(snapshot):
        raise MutationError("budget_exceeded", issues[0]["message"], issues[0]["required_action"])

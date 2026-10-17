"""Validate a read-only assessment for display; independent of ERP and retrieval."""

import math
from datetime import datetime


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value, low, high=math.inf):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _time(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("需要带时区的 ISO 时间")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("需要时区")
    return parsed


def valid_references(references: list, at: datetime) -> bool:
    """Check citation identity, score and validity at the recorded query time."""
    versions = {}
    for reference in references:
        if not isinstance(reference, dict):
            return False
        if not all(
            _text(reference.get(key))
            for key in ("source", "title", "text", "policy_id", "version")
        ):
            return False
        if not _number(reference.get("score"), -1, 1):
            return False
        start = _time(reference.get("effective_from"))
        end = reference.get("effective_until")
        if start > at or (end is not None and _time(end) <= at):
            return False
        identity = reference["policy_id"]
        version = (reference["version"], reference["effective_from"], end)
        if identity in versions and versions[identity] != version:
            return False
        versions[identity] = version
    return True


def policy_search_fields(arguments: dict, result: dict) -> dict | None:
    try:
        if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
            return None
        query = arguments.get("query")
        if not _text(query) or result.get("query") != query.strip():
            return None
        at = _time(result.get("queried_at"))
        state = result.get("policy_state")
        if state not in {"matched", "no_match", "unavailable", "inactive", "conflict"}:
            return None
        references = result.get("matches")
        k = arguments.get("k", 3)
        if type(k) is not int or not 1 <= k <= 5:
            return None
        if not isinstance(references, list) or len(references) > k:
            return None
        if bool(references) != (state == "matched") or not valid_references(references, at):
            return None
        if not isinstance(result.get("note"), str):
            return None
        return {"search": result}
    except (ValueError, TypeError, KeyError):
        return None


def assessment_fields(arguments: dict, result: dict) -> dict | None:
    try:
        if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
            return None
        at = _time(result.get("assessed_at"))
        order = result.get("order")
        if not isinstance(order, dict):
            return None
        if not all(_text(order.get(key)) for key in ("order_id", "customer", "status")):
            return None
        if not isinstance(arguments.get("order_id"), str):
            return None
        if order["order_id"] != arguments["order_id"].strip():
            return None
        if not _number(order.get("total_amount"), 0):
            return None
        status = order["status"]
        if status not in {"待付款", "待发货", "已发货", "已签收", "已取消", "已退款"}:
            return None
        allowed = result.get("business_allowed")
        if type(allowed) is not bool or allowed != (status in {"待付款", "待发货"}):
            return None
        reason = result.get("business_reason")
        if not isinstance(reason, dict) or not _text(reason.get("message")):
            return None
        if reason.get("code") != ("cancellable_status" if allowed else "invalid_transition"):
            return None
        state = result.get("policy_state")
        if state not in {"matched", "no_match", "unavailable", "inactive", "conflict"}:
            return None
        expected = (
            "ineligible" if not allowed else "eligible" if state == "matched" else "needs_review"
        )
        if result.get("decision") != expected or not _text(result.get("message")):
            return None
        references = result.get("references")
        if not isinstance(references, list) or bool(references) != (state == "matched"):
            return None
        if not valid_references(references, at):
            return None
        return {"assessment": result}
    except (ValueError, TypeError, KeyError):
        return None

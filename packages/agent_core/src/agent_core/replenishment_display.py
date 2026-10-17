"""Validate suggestions without importing business packages."""

import math
from datetime import datetime, timedelta


def _integer(value, low=0, high=2**53 - 1):
    return type(value) is int and low <= value <= high


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def replenishment_fields(arguments: dict, result: dict) -> dict | None:
    try:
        if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
            return None
        if result.get("timezone") != "Asia/Shanghai":
            return None
        at, start, end = [datetime.fromisoformat(result[key])
                          for key in ("assessed_at", "window_start", "window_end")]
        if any(time.utcoffset() != timedelta(hours=8) for time in (at, start, end)):
            return None
        for key, default, low, high in [("days", 30, 1, 365), ("coverage_days", 14, 1, 180),
                                        ("limit", 20, 1, 50), ("offset", 0, 0, 2**53 - 1)]:
            if not _integer(result.get(key), low, high) \
                    or result[key] != arguments.get(key, default):
                return None
        lead = result.get("lead_time_days")
        if (lead is not None and not _integer(lead, 0, 180)) \
                or lead != arguments.get("lead_time_days"):
            return None
        if at != end or end - start != timedelta(days=result["days"]):
            return None
        if not all(_text(result.get(key)) for key in ("sales_basis", "formula")):
            return None
        for key in ("assumptions", "limitations"):
            if not isinstance(result.get(key), list) or not result[key] \
                    or not all(_text(item) for item in result[key]):
                return None
        items = result.get("items")
        total, returned = result.get("total"), result.get("returned")
        if not isinstance(items, list) or not _integer(total) or not _integer(returned):
            return None
        if returned != len(items) \
                or returned != min(result["limit"], max(0, total-result["offset"])):
            return None
        if type(result.get("has_more")) is not bool \
                or result["has_more"] != (result["offset"] + returned < total):
            return None
        seen = set()
        requested = arguments.get("skus")
        if requested is not None:
            requested = {sku.strip() for sku in requested}
            if total != len(requested):
                return None
        for item in items:
            if not isinstance(item, dict) or not all(
                _text(item.get(key)) for key in ("sku", "name", "product_status", "reason")
            ):
                return None
            sku = item["sku"]
            if sku in seen or (requested is not None and sku not in requested):
                return None
            seen.add(sku)
            stock, sold = item.get("stock_quantity"), item.get("sold_quantity")
            if any(value is not None and not _integer(value) for value in (stock, sold)):
                return None
            daily = item.get("daily_sales")
            if sold is None:
                if daily is not None:
                    return None
            elif not _number(daily) or abs(daily - sold / result["days"]) > 0.000051:
                return None
            decision = item.get("decision")
            if decision not in {"reorder", "sufficient", "no_sales", "off_sale", "data_issue"}:
                return None
            if decision in {"reorder", "sufficient"}:
                if stock is None or sold is None or sold <= 0 or item["product_status"] != "在售":
                    return None
                target = (sold * (result["coverage_days"] + (lead or 0)) + result["days"] - 1) \
                    // result["days"]
                if not _integer(item.get("target_quantity")) or item["target_quantity"] != target:
                    return None
                if not _integer(item.get("suggested_quantity")) \
                        or item["suggested_quantity"] != max(0, target-stock):
                    return None
                if (decision == "reorder") != (target > stock):
                    return None
                remaining = item.get("estimated_days_remaining")
                if not _number(remaining) or abs(remaining-stock*result["days"]/sold) > 0.00501:
                    return None
                risk = item.get("stockout_before_arrival")
                if lead is None:
                    if risk is not None:
                        return None
                elif type(risk) is not bool or risk != (stock*result["days"] < sold*lead):
                    return None
            else:
                if any(item.get(key) is not None for key in (
                    "target_quantity", "suggested_quantity", "estimated_days_remaining",
                    "stockout_before_arrival",
                )):
                    return None
                if decision == "no_sales" and (
                    sold != 0 or stock is None or item["product_status"] != "在售"
                ):
                    return None
                if decision == "off_sale" and item["product_status"] != "已下架":
                    return None
        return {"suggestion": result}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None

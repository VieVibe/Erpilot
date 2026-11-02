"""Bind catalogue resolution to this turn's actual user text and tool result."""

import hashlib
import json
from copy import deepcopy

from erp_store.order_request import HINT, needs_resolution, parse_request
from pydantic import BaseModel, ConfigDict


class ResolveOrderItemsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def current_text(messages):
    source = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(HINT)
    return source


def resolution_source(arguments, messages, history):
    source = current_text(messages)
    return {"source_user_text": source,
            "source_user_sha256": hashlib.sha256(source.encode()).hexdigest()}


def required_resolution(messages, history):
    try:
        source = current_text(messages)
    except ValueError:
        return None
    if (needs_resolution(source)
            and not any(r.get("name") == "resolve_order_items" for r in history)):
        return {}
    return None


def observed_resolution(source, history):
    for record in reversed(history):
        if record.get("name") != "resolve_order_items" or not record.get("ok"):
            continue
        result = json.loads(record['content'])
        if result.get("source_user_sha256") != hashlib.sha256(source.encode()).hexdigest():
            raise ValueError("商品核对不属于本轮要求；" + HINT)
        if result.get("status") != "resolved":
            raise ValueError("商品或件数尚未明确，草稿未保存；" + HINT)
        parsed = parse_request(source)
        return parsed, deepcopy(result['creation_guard'])
    return None


def check_resolved_arguments(arguments, resolved):
    parsed, guard = resolved
    items = arguments.get("items")
    if (not isinstance(items, list) or any(
        not isinstance(r, dict) or not isinstance(r.get("sku"), str)
        or type(r.get("quantity")) is not int for r in items
    ) or [{"sku": r['sku'].strip(), "quantity": r['quantity']} for r in items] != guard['items']):
        raise ValueError("商品、顺序或件数与本轮实际商品核对不一致，草稿未保存")
    if arguments.get("customer") != parsed['customer']:
        raise ValueError("客户与本轮完整要求不一致，草稿未保存")
    return guard

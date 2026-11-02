"""Replay candidate selections and their complete deltas from actual source messages."""

import re
from copy import deepcopy

from erp_store.intent_contract import Intent, Slot, SourceMessage, SourceSpan, Unresolved
from erp_store.intent_rules import (
    analyze_rules,
    field_values,
    quantity_reply,
    selection_reply,
    slot_reply,
)
from erp_store.order_request import parse_request, quantity


def selection_events(data):
    messages = [SourceMessage.model_validate(m) for m in data["messages"]]
    if not 1 <= len(messages) <= 102:
        raise ValueError("补充轮次已达上限，请重新核对完整请求")
    if len({m.message_id for m in messages}) != len(messages):
        raise ValueError("同一轮来源不能重复消费候选问题")
    base = data["initial_resolution"]
    unresolved = deepcopy(base.get("issues", []))
    allowed = {"ambiguous_product"}
    if data.get("creation_slots_version") == 1:
        allowed.add("missing_quantity")
    if any(r["code"] not in allowed for r in unresolved):
        raise ValueError("待澄清请求包含不能用编号解决的问题")
    events = []
    for message in messages[1:]:
        match = selection_reply(message.text)
        count = quantity_reply(message.text)
        if count and unresolved and unresolved[0]["code"] == "missing_quantity":
            issue = unresolved.pop(0)
            events.append({"message": message, "item_number": issue["item_number"],
                           "sku": None, "span": None,
                           "quantity": quantity(count["number"]),
                           "quantity_span": SourceSpan(
                               message_id=message.message_id, start=count.start(), end=count.end(),
                               quote=message.text[count.start():count.end()]),
                           "ignored_spans": [count.span()]})
            continue
        if not match:
            span = slot_reply(message.text)
            if span is None:
                raise ValueError("补充内容不属于候选或槽位，请核对完整要求")
            events.append({"message": message, "sku": None, "span": None,
                           "ignored_spans": [span] if span[1] else []})
            continue
        if not unresolved:
            raise ValueError("没有有效候选问题，请核对完整要求")
        issue = unresolved.pop(0)
        if issue["code"] != "ambiguous_product":
            raise ValueError("当前问题需要最终件数，不能用候选编号代替")
        if match.groupdict().get("sku") is not None:
            numbers = [i for i, row in enumerate(issue["candidates"], 1)
                       if row["sku"] == match["sku"]]
            if len(numbers) != 1:
                raise ValueError("该 SKU 不属于当前展示的候选，请核对选择范围")
            number = numbers[0]
        else:
            number = quantity(match["number"])
        if issue.get("has_more") or not 1 <= number <= len(issue["candidates"]):
            raise ValueError("候选编号超出已展示范围，请选择明确 SKU")
        event = {"message": message, "item_number": issue["item_number"],
                 "sku": issue["candidates"][number - 1]["sku"],
                 "span": SourceSpan(message_id=message.message_id, start=match.start(),
                                    end=match.end(),
                                    quote=message.text[match.start():match.end()]),
                 "ignored_spans": [match.span()]}
        suffix = re.match(r"[，,；;\s]*", message.text[match.end():])
        count = quantity_reply(message.text, start=match.end() + suffix.end())
        if (count and unresolved and unresolved[0]["code"] == "missing_quantity"
                and unresolved[0]["item_number"] == issue["item_number"]):
            unresolved.pop(0)
            event.update(quantity=quantity(count["number"]), quantity_span=SourceSpan(
                message_id=message.message_id, start=count.start(), end=count.end(),
                quote=message.text[count.start():count.end()]))
            event["ignored_spans"].append(count.span())
        events.append(event)
    return events, unresolved


def replay_selection_messages(data):
    events, unresolved = selection_events(data)
    return {str(e["item_number"]): e["sku"] for e in events if e["span"] is not None}, unresolved


def replay_quantities(data):
    events, _ = selection_events(data)
    return {str(e["item_number"]): e["quantity"] for e in events if "quantity" in e}


def parsed_creation(data):
    return parse_request(data["messages"][0]["text"], extended=True,
                         allow_incomplete=data.get("creation_slots_version") == 1)


def question_fields(issue):
    if issue["code"] == "missing_quantity":
        return {"question": (f"第 {issue['item_number']} 项（{issue['product_query']}）"
                             "缺少明确件数。"
                             "请回复最终件数，例如“四件”；箱、包等单位请先核实后提供总件数。"),
                "candidates": []}
    return {"question": f"请为第 {issue['item_number']} 项选择唯一商品：",
            "candidates": issue["candidates"]}


def literal_origin(message, entry):
    start = message.text.index(entry["quote"])
    return {"kind": "user_literal", **entry, "source_user_sha256": message.sha256,
            "message_id": message.message_id, "start": start, "end": start + len(entry["quote"])}


def merge_creation_messages(data, resolution):
    """The original requirement stays intact. Each reply is verified as an atomic delta.

    Scalar deltas can be kept while another product still needs a choice. Quantity
    edits require the complete catalogue binding, and cannot append or replace items.
    """
    messages = [SourceMessage.model_validate(m) for m in data["messages"]]
    original = messages[0]
    events, unresolved = selection_events(data)
    guard = resolution.get("creation_guard")
    bound = guard["requirements"] if guard else []
    initial = {"status": "resolved", "items": deepcopy(guard["items"]) if guard else [],
               "creation_guard": {"requirements": bound}}
    frame, expected, literals, _ = analyze_rules(
        original.text, session_id=data["session_id"], request_id=original.message_id,
        resolution=initial,
        allow_incomplete_creation=data.get("creation_slots_version") == 1,
    )
    if frame.decision != "ready":
        raise ValueError(frame.unresolved[0].question)
    origins = {k: literal_origin(original, v) for k, v in literals.items()}
    item_origins, cursor = {}, 0
    for row in bound:
        start = original.text.index(row["quote"], cursor)
        cursor = start + len(row["quote"])
        item_origins[row["sku"]] = [SourceSpan(message_id=original.message_id, start=start,
                                               end=cursor, quote=row["quote"]).model_dump()]
    names = {row["sku"]: row["product_name"] for row in bound}
    for event in events:
        message, prefix = event["message"], event["span"]
        previous = {**expected, "items": [{**row, "name": names[row["sku"]]}
                                          for row in expected["items"]]}
        delta_frame, after, literals, requirements = analyze_rules(
            message.text, session_id=data["session_id"], request_id=message.message_id,
            previous=previous,
            ignored_spans=event["ignored_spans"],
            allow_preserve_only=prefix is not None or "quantity" in event,
            implicit_sku=event["sku"],
            allow_row_references=False,
        )
        if delta_frame.decision != "ready":
            raise ValueError(delta_frame.unresolved[0].question)
        if "quantity" in event and bound:
            target = bound[event["item_number"] - 1]["sku"]
            if any(r["sku"] == target and r["quantity"] != event["quantity"]
                   for r in requirements):
                raise ValueError("本轮对同一商品提供了不同件数，请明确唯一值")
        if [r["sku"] for r in after["items"]] != [r["sku"] for r in expected["items"]]:
            raise ValueError("选择回复不能增删或替换原清单商品，请重新核对完整请求")
        expected = field_values(after)
        origins.update({k: literal_origin(message, v) for k, v in literals.items()})
        if prefix and event["sku"] in item_origins:
            item_origins[event["sku"]].append(prefix.model_dump())
        if "quantity_span" in event and bound:
            sku = bound[event["item_number"] - 1]["sku"]
            item_origins[sku].append(event["quantity_span"].model_dump())
            frame.slots.append(Slot(field=f"items.{sku}.quantity", value=event["quantity"],
                                    evidence=[event["quantity_span"]]))
        for requirement in requirements:
            start = message.text.index(requirement["quote"])
            item_origins[requirement["sku"]].append(SourceSpan(
                message_id=message.message_id, start=start,
                end=start + len(requirement["quote"]), quote=requirement["quote"],
            ).model_dump())
        frame.slots.extend(delta_frame.slots)
        frame.intents.append(Intent(domain="order_draft", operation="create",
                                    dialogue_act="select_candidate" if prefix else "supply_slot"))
        frame.intents.extend(i for i in delta_frame.intents if i.domain == "order")
    frame.source_messages = messages
    if unresolved or resolution.get("status") != "resolved":
        frame.decision = "clarify"
        frame.allowed_next_tools = ["resolve_order_items"]
        frame.unresolved = [Unresolved(code=r["code"], question=question_fields(r)["question"])
                            for r in unresolved]
    requirements = [{**row, "evidence_refs": item_origins[row["sku"]]}
                    for row in expected["items"]]
    return frame, expected, origins, requirements


def preserved_fields(data, analyzed):
    expected = analyzed[1]
    pending_items = deepcopy(parsed_creation(data)["requirements"])
    quantities = replay_quantities(data)
    for index, row in enumerate(pending_items, 1):
        row["quantity"] = quantities.get(str(index), row["quantity"])
    return {**{k: expected[k] for k in ("customer", "note", "budget_limit")},
            "items": (deepcopy(expected["items"]) if expected["items"] else
                      pending_items)}

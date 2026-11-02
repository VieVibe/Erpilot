"""Bounded literal checks, not general natural-language intent understanding.

Only the latest user message can introduce a literal requirement. Existing fields
come from actual reads in this turn, never from model-supplied evidence. Unsupported
creation wording remains explicitly unverified; changing an existing customer or
note requires a supported explicit instruction.
"""

import hashlib
import json
import re
from dataclasses import replace
from functools import partial

from erp_store.draft_budget import normalize_budget
from erp_store.intent_literals import _CLAUSE, _NOTES, _mask_notes, literal_requirements

from mcp_erp.draft_creation import validate_creation_items
from mcp_erp.draft_items import validate_item_changes
from mcp_erp.order_language import (
    ResolveOrderItemsParams,
    check_resolved_arguments,
    observed_resolution,
    required_resolution,
    resolution_source,
)

# Deliberately bounded to an explicit query clause and a real draft-id shape.
# Quoted notes and old conversation messages do not select a prerequisite read.
_HISTORY_QUERY = re.compile(
    _CLAUSE + r"(?:只读)?(?:查询|查看|读取)\s*(?:指定)?草稿\s*[`「“\"]?"
    r"(?P<draft_id>[0-9a-fA-F]{32})[`」”\"]?\s*的?\s*(?:全部)?"
    r"(?:修改记录|历史记录|版本历史)"
)


def _history_targets(messages):
    question = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
    if not isinstance(question, str):
        return "", []
    # A quoted note is data even if it contains a query-shaped instruction.
    for pattern in _NOTES:
        question = pattern.sub(" ", question)
    return question, [m["draft_id"] for m in _HISTORY_QUERY.finditer(question)]


def required_task_read(messages, history):
    question, targets = _history_targets(messages)
    if targets and "先查询当前任务" in question and not any(
        row.get("name") == "get_order_task" for row in history
    ):
        return {}
    return None


def required_history_read(messages, history):
    question, targets = _history_targets(messages)
    if "先查询当前任务" in question and not any(
        row.get("name") == "get_order_task" for row in history
    ):
        return None
    for draft_id in targets:
        if not any(
            row.get("name") == "get_order_draft_history"
            and row.get("arguments", {}).get("draft_id") == draft_id
            for row in history
        ):
            size = re.search(r"(?:limit\s*=\s*|每页\s*)(\d{1,3})", question)
            cursor = re.search(r"before_revision\s*=\s*(\d+)", question)
            arguments = {"draft_id": draft_id, "limit": int(size[1]) if size else 5}
            if cursor:
                arguments["before_revision"] = int(cursor[1])
            return arguments
    return None



def _observed_draft(arguments, history):
    for record in reversed(history):
        if not record.get("ok") or record.get("name") not in {"get_order_task", "get_order_draft"}:
            continue
        try:
            payload = json.loads(record["content"])
            draft = payload.get("draft") if record["name"] == "get_order_task" else payload
            if (
                isinstance(draft, dict)
                and draft.get("draft_id") == arguments["draft_id"]
                and draft.get("revision") == arguments["expected_revision"]
                and "customer" in draft and "note" in draft
            ):
                return draft
        except (ValueError, TypeError, KeyError):
            continue
    raise ValueError("先在本轮查询目标草稿的当前版本，再修改；草稿未修改")


def ensure_task_selection(draft_id, question, task):
    if task.get("status") != "ambiguous":
        return
    # Quoted data cannot select a task. Duplicate customer names require an ID.
    from mcp_erp.draft_items import _mask_quotes

    text = _mask_quotes(_mask_notes(question))
    candidates = task["candidates"]
    selected = [row for row in candidates if row["draft_id"] in text]
    if not selected:
        selected = [row for row in candidates if row["customer"] in text]
    if len(selected) != 1 or selected[0]["draft_id"] != draft_id:
        raise ValueError("当前存在多份草稿，请明确唯一客户或草稿标识后再操作；未修改或提交")


def validate_draft_arguments(tool_name, arguments, messages, history):
    question = next(
        (message.get("content") for message in reversed(messages) if message.get("role") == "user"),
        None,
    )
    if not isinstance(question, str) or not question.strip():
        raise ValueError("缺少本轮用户原话，不能保存草稿")
    literals = literal_requirements(question)
    fields = {}
    previous = _observed_draft(arguments, history) if tool_name != "prepare_order_draft" else None
    resolved = observed_resolution(question, history) if previous is None else None
    if resolved:
        parsed, _ = resolved
        literals["customer"] = {"value": parsed['customer'], "quote": parsed['customer_quote']}
        if "note" not in literals and arguments.get("note") is not None:
            raise ValueError("本轮未指定备注，不能自行补写；草稿未保存")
    if previous is not None:
        for row in reversed(history):
            if row.get("ok") and row.get("name") == "get_order_task":
                ensure_task_selection(arguments["draft_id"], question, json.loads(row["content"]))
                break
    if tool_name == "revise_order_draft_quantity":
        number = arguments.get("item_number")
        if type(number) is not int or not 1 <= number <= len(previous["items"]):
            raise ValueError("行号须取自本轮目标草稿，草稿未修改")
        if arguments.get("expected_sku") != previous["items"][number - 1]["sku"]:
            raise ValueError("必须传入本轮当前版本该行的 expected_sku，草稿未修改")
    for field in ("customer", "note"):
        if field in literals:
            requirement = literals[field]
            expected = requirement["value"]
            evidence = {"kind": "user_literal", **requirement}
        elif previous is not None:
            expected = previous[field]
            evidence = {
                "kind": "observed_draft", "value": expected,
                "draft_id": previous["draft_id"], "revision": previous["revision"],
            }
        else:
            continue
        actual = (
            previous[field] if tool_name == "revise_order_draft_quantity" else arguments.get(field)
        )
        if actual != expected:
            label = "客户全称" if field == "customer" else "备注"
            raise ValueError(
                f"{label}与用户原文或本轮草稿不一致，必须逐字保留"
                f" {json.dumps(expected, ensure_ascii=False)}；草稿未保存。"
                "如需更改，请先让用户明确说明客户改为…或备注改为「…」"
            )
        fields[field] = evidence
    if arguments.get("clear_budget") and arguments.get("budget_limit") is not None:
        raise ValueError("不能同时设置和取消预算上限")
    prior_budget = previous.get("budget_limit") if previous else None
    actual_budget = (prior_budget if tool_name == "revise_order_draft_quantity" else
                     None if arguments.get("clear_budget") else
                     arguments.get("budget_limit") if arguments.get("budget_limit") is not None
                     else prior_budget)
    requirement = literals.get("budget_limit")
    expected_budget = requirement["value"] if requirement else prior_budget
    if normalize_budget(actual_budget) != expected_budget:
        raise ValueError("预算上限与本轮明确要求或已保存预算不一致；"
                         "请明确预算上限为…元，或取消预算上限。草稿未保存")
    if requirement:
        fields["budget_limit"] = {"kind": "user_literal", **requirement}
    elif previous is not None:
        fields["budget_limit"] = {
            "kind": "observed_draft", "value": prior_budget,
            "draft_id": previous["draft_id"], "revision": previous["revision"],
        }
    item_guard = validate_item_changes(
        tool_name, arguments, previous, _mask_notes(question), question,
    ) if previous is not None else None
    creation_guard = None
    if previous is None:
        creation_guard = (check_resolved_arguments(arguments, resolved) if resolved
                          else validate_creation_items(arguments, _mask_notes(question), question))
    return {
        "contract": "draft-literals-1",
        "source_user_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "source_user_text": question,
        "fields": fields,
        **({"item_guard": item_guard} if item_guard is not None else {}),
        **({"creation_guard": creation_guard} if creation_guard is not None else {}),
        "unverified_fields": [
            field for field in ("customer", "note") if field not in fields
        ] + ([] if creation_guard else ["items"]),
    }


def bind_draft_constraints(tools):
    return [
        replace(tool, call_validator=partial(validate_draft_arguments, tool.name))
        if tool.name in {
            "prepare_order_draft", "revise_order_draft", "revise_order_draft_quantity",
        } else replace(tool, params_model=ResolveOrderItemsParams, call_validator=resolution_source,
                       required_read=required_resolution)
        if tool.name == "resolve_order_items"
        else replace(tool, required_read=required_history_read)
        if tool.name == "get_order_draft_history"
        else replace(tool, required_read=required_task_read) if tool.name == "get_order_task"
        else tool
        for tool in tools
    ]

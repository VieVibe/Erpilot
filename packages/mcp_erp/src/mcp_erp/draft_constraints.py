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
from decimal import Decimal
from functools import partial

from erp_store.draft_budget import normalize_budget

_CLAUSE = r"(?:^|[，,。；;\n])\s*(?:请\s*)?"
_CREATE_CUSTOMER = re.compile(
    _CLAUSE + r"(?:给|为)\s*(?P<value>[^，,。；;\n]{1,80}?)\s*"
    r"(?:保存|创建|新建|准备|生成)(?:一(?:份|个|张))?(?:报价|订单)?草稿"
)
_CHANGE_CUSTOMER = re.compile(
    _CLAUSE + r"(?:把|将)?客户(?:名|全称)?\s*"
    r"(?:修改为|设置为|改成|改为|设为|为|是|[:：])\s*"
    r"(?P<value>[^，,。；;\n]{1,80}?)(?=$|[，,。；;\n])"
)
_QUOTE_PAIRS = (("「", "」"), ("“", "”"), ('"', '"'), ("'", "'"))
_NOTES = [
    re.compile(
        _CLAUSE + r"(?:把|将|修改)?备注(?:原文|内容)?\s*"
        r"(?:修改为|设置为|改成|改为|设为|为|是|[:：])?\s*"
        + re.escape(left) + r"(?P<value>[^" + re.escape(left + right) + r"]*)"
        + re.escape(right)
    )
    for left, right in _QUOTE_PAIRS
]
_CLEAR_NOTE = re.compile(
    _CLAUSE + r"(?:(?:清空|删除|去掉)备注|(?:把|将)?备注(?:清空|删除))(?=$|[，,。；;\n])"
)
_BUDGET = re.compile(
    _CLAUSE + r"(?:把|将)?(?:订单)?(?:总预算|预算(?:上限)?|总价上限|总价)\s*"
    r"(?:修改为|调整为|提高到|提高至|降低到|降低至|设置为|改成|改为|设为|不得超过|不超过|最多|为|[:：])\s*"
    r"(?:人民币|CNY|[¥￥])?\s*(?P<value>\d+(?:\.\d+)?)\s*(?:元)?(?=$|[，,。；;\n])"
)
_CLEAR_BUDGET = re.compile(
    _CLAUSE + r"(?:取消|清空|移除)预算上限(?=$|[，,。；;\n])"
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


def _one(values, field):
    if not values:
        return None
    if len({value for value, _ in values}) != 1:
        raise ValueError(f"本轮存在多个不同的{field}要求，请明确唯一值后再保存；草稿未修改")
    return values[-1]


def _customer_literal(value):
    value = value.strip()
    for left, right in _QUOTE_PAIRS:
        if value.startswith(left) and value.endswith(right):
            return value[len(left):-len(right)].strip()
    return value


def literal_requirements(question):
    """Recognize documented positive clauses; quoted notes never act as commands."""
    notes, spans = [], []
    for pattern in _NOTES:
        for match in pattern.finditer(question):
            notes.append((match["value"], match.group().lstrip("，,。；;\n ")))
            spans.append(match.span())
    masked = list(question)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    remaining = "".join(masked)
    notes.extend((None, match.group().strip()) for match in _CLEAR_NOTE.finditer(remaining))
    customers = [
        (_customer_literal(match["value"]), match.group().strip("，,。；;\n "))
        for pattern in (_CREATE_CUSTOMER, _CHANGE_CUSTOMER)
        for match in pattern.finditer(remaining)
    ]
    budgets = [(normalize_budget(Decimal(m["value"])), m.group().strip("，,。；;\n "))
               for m in _BUDGET.finditer(remaining)]
    budgets.extend((None, m.group().strip("，,。；;\n "))
                   for m in _CLEAR_BUDGET.finditer(remaining))
    masked_budget = _CLEAR_BUDGET.sub(" ", _BUDGET.sub(" ", remaining))
    if re.search(_CLAUSE + r"(?:订单)?(?:总预算|预算|总价上限|总价不超过|"
                 r"(?:取消|清空|移除|删除)预算)", masked_budget):
        raise ValueError("预算表达无法核验；请明确说预算上限为3000元、预算改为3500元或取消预算上限")
    labels = {"customer": "客户", "note": "备注", "budget_limit": "预算"}
    return {
        field: {"value": chosen[0], "quote": chosen[1]}
        for field, values in (("customer", customers), ("note", notes), ("budget_limit", budgets))
        if (chosen := _one(values, labels[field])) is not None
    }


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
    return {
        "contract": "draft-literals-1",
        "source_user_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "source_user_text": question,
        "fields": fields,
        "unverified_fields": [
            field for field in ("customer", "note", "items") if field not in fields
        ],
    }


def bind_draft_constraints(tools):
    return [
        replace(tool, call_validator=partial(validate_draft_arguments, tool.name))
        if tool.name in {
            "prepare_order_draft", "revise_order_draft", "revise_order_draft_quantity",
        } else replace(tool, required_read=required_history_read)
        if tool.name == "get_order_draft_history"
        else replace(tool, required_read=required_task_read) if tool.name == "get_order_task"
        else tool
        for tool in tools
    ]

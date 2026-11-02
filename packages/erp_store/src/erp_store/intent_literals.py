"""Shared literal field grammar; quote values remain original user data."""

import re
from decimal import Decimal

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
_QUOTE_PAIRS = (("『", "』"), ("‘", "’"), ("「", "」"), ("“", "”"), ('"', '"'), ("'", "'"))


def _note_patterns(extra=""):
    return [
        re.compile(
            _CLAUSE + r"(?:把|将|修改)?备注(?:原文|内容)?\s*"
            + r"(?:" + extra + r"修改为|设置为|改成|改为|设为|为|是|[:：])?\s*"
            + re.escape(left) + r"(?P<value>[^" + re.escape(left + right) + r"]*)"
            + re.escape(right)
        ) for left, right in _QUOTE_PAIRS
    ]


_NOTES = _note_patterns()
_INTENT_NOTES = _note_patterns("写成|写为|写|")
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


def _mask_notes(question):
    masked = list(question)
    for pattern in _NOTES:
        for match in pattern.finditer(question):
            start, end = match.span()
            masked[start:end] = " " * (end - start)
    return "".join(masked)


def literal_requirements(question, *, strict_scopes=False, ignored_spans=()):
    """Recognize documented positive clauses; quoted notes never act as commands."""
    from erp_store.intent_contract import scan_source

    scoped = scan_source(question) if strict_scopes else None
    view = list(question)
    for start, end in ignored_spans:
        view[start:end] = " " * (end - start)
    view = "".join(view)
    if scoped and ignored_spans:
        commands = list(scoped.command_text)
        for start, end in ignored_spans:
            commands[start:end] = " " * (end - start)
        scoped = scoped.model_copy(update={"command_text": "".join(commands)})

    def permitted(match):
        start = match.start() + len(match.group()) - len(match.group().lstrip("，,。；;\n "))
        return not scoped or not any(
            left <= start < right or left < match.end() < right
            for left, right in scoped.protected
        )

    notes, spans = [], []
    for pattern in _INTENT_NOTES if strict_scopes else _NOTES:
        for match in pattern.finditer(view):
            if not permitted(match):
                continue
            notes.append((match["value"], match.group().lstrip("，,。；;\n ")))
            spans.append(match.span())
    masked = list(view)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    remaining = "".join(masked)
    commands = scoped.command_text if scoped else remaining
    notes.extend((None, match.group().strip()) for match in _CLEAR_NOTE.finditer(commands))
    customers = [
        (_customer_literal(match["value"]), match.group().strip("，,。；;\n "))
        for pattern in (_CREATE_CUSTOMER, _CHANGE_CUSTOMER)
        for match in pattern.finditer(remaining)
        if permitted(match)
    ]
    budgets = [(normalize_budget(Decimal(m["value"])), m.group().strip("，,。；;\n "))
               for m in _BUDGET.finditer(commands)]
    budgets.extend((None, m.group().strip("，,。；;\n "))
                   for m in _CLEAR_BUDGET.finditer(commands))
    masked_budget = _CLEAR_BUDGET.sub(" ", _BUDGET.sub(" ", commands))
    if re.search(_CLAUSE + r"(?:订单)?(?:总预算|预算|总价上限|总价不超过|"
                 r"(?:取消|清空|移除|删除)预算)", masked_budget):
        raise ValueError("预算表达无法核验；请明确说预算上限为3000元、预算改为3500元或取消预算上限")
    labels = {"customer": "客户", "note": "备注", "budget_limit": "预算"}
    return {
        field: {"value": chosen[0], "quote": chosen[1]}
        for field, values in (("customer", customers), ("note", notes), ("budget_limit", budgets))
        if (chosen := _one(values, labels[field])) is not None
    }


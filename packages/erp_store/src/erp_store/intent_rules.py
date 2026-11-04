"""Deterministic order intent analysis and complete-snapshot reduction.

Unsupported residue is a clarification, never an authorization to save a subset.
Both the application adapter and the SQLite transaction run these same rules.
"""

import re
from copy import deepcopy

from erp_store.intent_contract import (
    Intent,
    Slot,
    SourceMessage,
    SourceSpan,
    Unresolved,
    VerifiedIntentFrame,
    digest,
    scan_source,
)
from erp_store.intent_items import _requirements
from erp_store.intent_literals import literal_requirements
from erp_store.mutations import MutationError
from erp_store.order_request import parse_request

_SELECTION_HEAD = (r"\s*(?:(?:选|选择)(?:第)?|第)"
                   r"(?P<number>[1-9][0-9]?|[一二三四五六七八九十]+)(?:个|项)?")
SELECTION = re.compile(_SELECTION_HEAD
                       + r"(?:[，,；;\s]*(?:其他|其余)(?:照旧|不变))?[。！!\s]*")
_SELECTION_REPLY = re.compile(_SELECTION_HEAD + r"(?=$|[，,；;。！!\s])")
_SKU_CHOICE = re.compile(r"\s*(?:(?:选|选择)\s*)?SKU\s+"
                          r"(?P<sku>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})(?=$|[，,；;。！!])")
_PENDING_PREFIX = re.compile(r"\s*待完成请求\s*[:：]\s*")
_QUANTITY_REPLY = re.compile(
    r"\s*(?:待完成请求\s*[:：]\s*)?"
    r"(?:(?:最终)?(?:数量|件数)\s*(?:改成|改为|为|是|[:：])?\s*)?"
    r"(?P<number>[1-9][0-9]{0,8}|[零一二两三四五六七八九十百千]+)\s*件"
    r"(?=$|[，,；;。！!\s])"
)
_SLOT_START = re.compile(
    r"\s*(?:请\s*)?(?:(?:把|将|修改)?(?:客户(?:名|全称)?|备注(?:原文|内容)?|"
    r"(?:订单)?(?:总预算|预算(?:上限)?|总价(?:上限)?)|数量)\s*"
    r"(?:改|修改|调整|设置|设|写|为|是|[:：]|不超过|不得超过|最多|提高|降低|清空|删除)|"
    r"(?:清空|删除|去掉)备注|(?:取消|清空|移除)预算上限)"
)
_SKU_SLOT = re.compile(r"\s*(?:请\s*)?(?:把|将)?(?:SKU\s+|商品\s*)")
WITHDRAW = re.compile(r"\s*(?:取消|撤回|算了|不要了)"
                      r"(?:这次|刚才的|待完成)?(?:请求|要求)?[。！!\s]*")
_PASSIVE = re.compile(r"如果|假如|假设|要是|除非|一旦|否则|例如|举例|示例|倘若|只要|"
                      r"只(?:读|查|查询|查看|解释|说明|分析|讨论)|先别(?:改|保存)|"
                      r"不要(?:修改|创建|保存)|(?:等|待).*(?:确认|同意|批准).*(?:后|再)")
_IGNORE = re.compile(r"(?:其他|其余)(?:信息|字段)?(?:都)?(?:保持)?不变|其他照旧|按(?:此|这个)顺序|"
                     r"只(?:存|保存)草稿|(?:先|暂时)?不提交(?:订单)?|不要提交|先别提交|"
                     r"[，,、。；;：:\s！!]+")
_REFRESH = re.compile(r"(?:请)?(?:刷新|更新)(?:当前)?(?:草稿)?报价")


def field_values(snapshot):
    return {"customer": snapshot["customer"], "note": snapshot.get("note"),
            "budget_limit": snapshot.get("budget_limit"),
            "items": [{"sku": row["sku"], "quantity": row["quantity"]}
                      for row in snapshot["items"]]}


def selection_reply(source):
    """Locate only the choice prefix; the complete remaining request still needs verification."""
    commands = scan_source(source).command_text
    return _SELECTION_REPLY.match(commands) or _SKU_CHOICE.match(commands)


def slot_reply(source):
    """A possible slot correction, not permission to apply any part of the text.

    A prefix identifies the pending request when saved drafts also exist. Every
    remaining clause must still pass the full delta rules before state changes.
    """
    commands = scan_source(source).command_text
    prefix = _PENDING_PREFIX.match(commands)
    if prefix:
        return prefix.span()
    # Unsupported packaging is still an attempted answer to a count question.
    # Classify it for clarification only; the delta verifier must reject conversion.
    if re.match(r"\s*[0-9一二两三四五六七八九十百千]+\s*(?:箱|包|盒|袋|套|瓶)"
                r"(?=$|[，,；;。！!\s])", commands):
        return (0, 0)
    if _QUANTITY_REPLY.match(commands) or _SLOT_START.match(commands) or (_SKU_SLOT.match(commands)
                                       and re.search(r"改|修改|调整|设置|保持|保留", commands)):
        return (0, 0)
    return None


def quantity_reply(source, *, start=0):
    """A count answer needs a current missing-count question before it can bind."""
    return _QUANTITY_REPLY.match(scan_source(source).command_text, start)


def _remaining(scoped, quotes):
    masked = list(scoped.command_text)
    for quote in quotes:
        # Each occurrence has the same grammar. Do not relocate a model's evidence.
        start = 0
        while (start := scoped.raw_text.find(quote, start)) >= 0:
            masked[start:start + len(quote)] = " " * len(quote)
            start += len(quote)
    return _IGNORE.sub("", "".join(masked))


def analyze_rules(source, *, session_id, request_id, previous=None, resolution=None,
                  ignored_spans=(), allow_preserve_only=False, implicit_sku=None,
                  reference=None, allow_row_references=True, allow_incomplete_creation=False):
    message = SourceMessage(message_id=request_id, text=source, sha256=digest(source))
    frame = VerifiedIntentFrame(request_id=request_id, session_id=session_id,
                                source_messages=[message], decision="clarify")
    try:
        scoped = scan_source(source)
        commands = list(scoped.command_text)
        for start, end in ignored_spans:
            commands[start:end] = " " * (end - start)
        scoped = scoped.model_copy(update={"command_text": "".join(commands)})
        if re.search(r"(?:不要|先别|不)提交", scoped.command_text):
            frame.intents.append(Intent(domain="order", operation="request_submit",
                                         modality="negated"))
        if re.search(r"(?:能不能|能否|可否|可不可以)取消", scoped.command_text):
            frame.intents.append(Intent(domain="order", operation="assess_cancel"))
        if re.search(r"如果|假如|只要", scoped.command_text) and re.search(
            r"提交|下单|建单", scoped.command_text,
        ):
            frame.intents.append(Intent(domain="order", operation="request_submit",
                                         modality="conditional"))
        if scoped.issues:
            raise ValueError("引号或代码边界未闭合，请补齐后再保存")
        if _PASSIVE.search(scoped.command_text):
            raise ValueError("条件、只读或否定要求不能授权当前草稿变更，请明确执行范围")
        literals = literal_requirements(source, strict_scopes=True, ignored_spans=ignored_spans)
        quotes = [v["quote"] for v in literals.values()]
        if previous is None:
            parsed = parse_request(source, extended=True,
                                   allow_incomplete=allow_incomplete_creation)
            quotes += [parsed["customer_quote"], *[r["quote"] for r in parsed["requirements"]]]
            literals["customer"] = {"value": parsed["customer"], "quote": parsed["customer_quote"]}
            if not resolution or resolution.get("status") != "resolved":
                raise ValueError("请先核对完整商品清单并选择唯一商品")
            expected = {"customer": parsed["customer"], "note": None, "budget_limit": None,
                        "items": deepcopy(resolution["items"])}
            requirements = resolution["creation_guard"]["requirements"]
            operation = "create"
        else:
            expected = field_values(previous)
            requirements = _requirements(scoped.command_text, source, previous["items"],
                                          implicit_sku=implicit_sku, reference=reference,
                                          strict_references=True, allow_rows=allow_row_references)
            quotes += [r["quote"] for r in requirements]
            by_sku = {r["sku"]: r for r in expected["items"]}
            for row in requirements:
                if row["sku"] in by_sku:
                    by_sku[row["sku"]]["quantity"] = row["quantity"]
                else:
                    expected["items"].append({"sku": row["sku"], "quantity": row["quantity"]})
            refreshes = list(_REFRESH.finditer(scoped.command_text))
            quotes += [m.group() for m in refreshes]
            if not (requirements or literals or refreshes or allow_preserve_only):
                raise ValueError("没有可以核验的明确修改，请指定目标与完整要求")
            operation = "revise"
        if _remaining(scoped, quotes):
            raise ValueError("还有未能核验的业务要求，请明确完整修改；未保存部分结果")
        for field, entry in literals.items():
            expected[field] = entry["value"]
            start = source.index(entry["quote"])
            frame.slots.append(Slot(field=field, value=entry["value"],
                                    operation="clear" if entry["value"] is None else "set",
                                    evidence=[SourceSpan(message_id=request_id, start=start,
                                                         end=start + len(entry["quote"]),
                                                         quote=entry["quote"])]))
        for index, row in enumerate(requirements):
            if (previous is None and allow_incomplete_creation
                    and parsed["requirements"][index]["quantity"] is None):
                continue  # The original product span cannot prove a later supplied count.
            start = source.index(row["quote"])
            frame.slots.append(Slot(
                field=f"items.{row['sku']}.quantity", value=row["quantity"],
                evidence=[SourceSpan(message_id=request_id, start=start,
                                     end=start + len(row["quote"]), quote=row["quote"])],
            ))
        frame.intents.insert(0, Intent(domain="order_draft", operation=operation))
        frame.decision = "ready"
        frame.allowed_next_tools = (["prepare_order_draft"] if previous is None else
                                    ["revise_order_draft", "revise_order_draft_quantity"])
        return frame, expected, literals, requirements
    except (ValueError, MutationError) as exc:
        frame.unresolved = [Unresolved(code="incomplete_intent", question=str(exc))]
        return frame, None, {}, []

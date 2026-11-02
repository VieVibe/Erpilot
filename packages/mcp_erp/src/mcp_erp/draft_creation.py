"""Explicit creation lists only; unrecognized requirements require clarification."""

import re

from mcp_erp.draft_items import _mask_quotes

_CREATE = re.compile(r"(?:保存|创建|新建|准备|生成)(?:一(?:份|个|张))?(?:报价|订单)?草稿\s*[:：]")
_PREFIX = re.compile(r"\s*(?:请\s*)?(?:(?:给|为)[^，,。；;:\n]{1,160})?\s*")
_ITEM = re.compile(
    r"(?:商品\s*)?(?:SKU\s*)?(?P<sku>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})"
    r"\s+(?P<quantity>[1-9]\d{0,8})\s*件"
)
_CONTROL = re.compile(
    r"按(?:此|这个)顺序|其他不变|只(?:存|保存)草稿|只保存|(?:先|暂时)?不提交(?:订单)?|不要提交|"
    r"备注.*|(?:预算(?:上限)?|总预算|总价)(?:\s|为|改|上限|[:：]).*"
)
_UNDECIDED = re.compile(
    r"如果|假如|假设|要是|除非|一旦|否则|例如|举例|示例|倘若|若|只要|或者|或是|还是|"
    r"(?:等|待)[^，,。；;\n]{0,32}(?:确认|批准|同意)(?:后|再)|"
    r"(?:不要|别|禁止|不许|暂不|先不|不)(?:再|要)?(?:创建|保存|新建|生成|准备)|"
    r"只(?:查询|查看|解释|说明|分析|讨论)|(?:不要|排除|除外|不含|不包括)(?:商品|SKU|\s*[A-Za-z0-9])"
)
HINT = ("创建商品清单无法核验，草稿未保存。请在本轮明确说"
        "『给客户保存报价草稿：A1001 2件，B2001 3件』，"
        "完整列出 SKU 和正整数件数；有条件、冲突或不确定的需求请先确认，"
        "也可在草稿表单中逐项核对。")


def validate_creation_items(arguments, instruction_text, source_text):
    text = _mask_quotes(instruction_text)
    markers = list(_CREATE.finditer(text))
    if len(markers) != 1 or _UNDECIDED.search(text):
        raise ValueError(HINT)
    marker = markers[0]
    if not _PREFIX.fullmatch(text[:marker.start()]):
        raise ValueError(HINT)
    # Text before the creation phrase can name the customer, but cannot hide items.
    if re.search(r"\d+\s*件|\bSKU\b", text[:marker.start()]):
        raise ValueError(HINT)
    requirements, tail = [], False
    for clause in re.finditer(r"[^，,、。；;\n]+", text[marker.end():]):
        value = clause.group().strip()
        if not value:
            continue
        match = _ITEM.fullmatch(value)
        if match and not tail:
            sku, quantity = match["sku"], int(match["quantity"])
            # Duplicate SKU entries are ambiguous rather than silently summed.
            if sku in {row["sku"] for row in requirements}:
                raise ValueError(HINT)
            start = (marker.end() + clause.start()
                     + len(clause.group()) - len(clause.group().lstrip()))
            end = start + len(value)
            requirements.append({"sku": sku, "quantity": quantity,
                                 "quote": source_text[start:end].strip()})
        elif _CONTROL.fullmatch(value):
            tail = True
        else:
            raise ValueError(HINT)
    if not requirements or len(requirements) > 100:
        raise ValueError(HINT)
    expected = [{"sku": row["sku"], "quantity": row["quantity"]} for row in requirements]
    # Compare with the same merge and whitespace rules as the domain layer.
    merged = {}
    for row in arguments["items"]:
        sku, quantity = row["sku"].strip(), row["quantity"]
        if type(quantity) is not int or quantity < 1:
            raise ValueError(HINT)
        merged[sku] = merged.get(sku, 0) + quantity
    if [{"sku": sku, "quantity": count} for sku, count in merged.items()] != expected:
        raise ValueError("创建草稿的商品、顺序或数量与本轮清单不一致；" + HINT)
    return {"contract": "draft-create-items-1", "items": expected,
            "requirements": requirements}

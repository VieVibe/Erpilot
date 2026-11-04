"""Bounded quantity instructions for an already observed draft.

The application resolves the target and amount from the current user's text.
Model arguments cannot authorize other line changes. This is deliberately not a
general intent parser; removing/reordering items uses the explicit form.
"""

import re

from erp_store.order_request import quantity as chinese_quantity

_NUMBER = r"(?:[1-9]\d{0,2}|[一二三四五六七八九十百]{1,5})"
_COUNT = r"(?:[1-9]\d*|[零一二两三四五六七八九十百千]{1,16})"
_PREFIX = r"(?:再)?(?:只把|只将|把|将|按|只修改|修改)?\s*(?:客户[^，,。；;\n]{1,64}的)?"
_ROW = (_PREFIX + r"(?P<current>当前(?:版本|草稿)?的?)?第(?P<number>" + _NUMBER + r")(?:行|个)")
_DISPLAY_ROW = (_PREFIX + r"(?P<view>刚才|上一轮)(?:展示|显示)的?第(?P<number>"
                + _NUMBER + r")(?:行|个)")
_SKU = (
    r"(?:(?:我确认修改上一轮展示的\s*)?SKU\s*|"
    + _PREFIX + r"(?:商品\s*)?(?:SKU\s*)?)"
    r"(?P<sku>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})"
)
_CHANGE = (r"(?:修改为|调整为|设置为|改成|改为|设为|为)\s*(?P<quantity>"
           + _COUNT + r")\s*(?:件)?")
_AMOUNT = r"的?\s*(?:把)?\s*(?:数量)?\s*" + _CHANGE
_PRESERVE = (r"的?\s*(?:数量)?\s*(?:保持|保留)\s*"
             r"(?:(?P<quantity>" + _COUNT
             + r")\s*件|(?:刚才|当前|原来|之前)的数量|原数量|不变)")
_NAME = _PREFIX + r"商品\s*(?P<name>[^，,。；;\n]{1,128}?)"
_LABEL = r"\s*(?:（(?P<label>[^（）\n]{1,128})）|\((?P<label_ascii>[^()\n]{1,128})\))?"
_ROW_SKU = (_ROW + r"\s+(?:SKU\s*)?"
            r"(?P<sku>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})" + _LABEL)
_TARGETS = [(re.compile(target), re.compile(target + r"\s*" + _AMOUNT),
             re.compile(target + r"\s*" + _PRESERVE))
            for target in (_ROW_SKU, _ROW, _DISPLAY_ROW, _NAME, _SKU + _LABEL)]
_QUANTITY = re.compile(r"(?:把)?数量\s*" + _CHANGE)
_ADD = re.compile(
    r"(?:在当前(?:版本|草稿)(?:中)?)?(?:新增|添加|加入)(?:商品\s*)?(?:SKU\s*)?"
    r"(?P<sku>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})\s+(?P<quantity>" + _COUNT + r")\s*件"
)
_POSITION = re.compile(r"放在第(?P<number>" + _NUMBER + r")行")
_UNSUPPORTED_EDIT = re.compile(
    r"(?:第" + _NUMBER + r"(?:行|个)|SKU\s*\S+|数量).*"
    r"(?:修改|更改|改成|改为|调整|设为|保持|保留)|"
    r"(?:放|插|排|移).*(?:第" + _NUMBER + r"行|开头|前面|中间)|"
    r"(?:新增|添加|加入|删除|移除|去掉)(?:商品|SKU)"
)
_CONDITIONAL = re.compile(
    r"如果|假如|假设|要是|除非|一旦|否则|例如|举例|示例|倘若|若|只要|或者|或是|还是|"
    r"(?:时|的话|之后|以后)[，,]|(?:等|待)[^，,。；;\n]{0,32}(?:确认|批准|同意|允许)(?:后|再)|"
    r"只(?:需|要)?(?:读|查询|查看|解释|说明|分析|讨论)|"
    r"(?:暂时|暂|先)?(?:别|不要|不许|禁止|不必|无需|勿|不)(?:再|要)?"
    r"(?:修改|更改|改动|调整|编辑|改|新增|添加|加入|追加)"
    r"(?:$|[，,。；;\n]|草稿|订单|商品|数量|任何)|"
    r"(?:不要|别|不许|禁止)(?:把|将)[^，,。；;\n]*(?:改|调整)"
)
_DIGITS = {char: value for value, char in enumerate("零一二三四五六七八九")}
_HINT = ("商品数量与本轮明确要求或当前草稿不一致，草稿未保存。"
         "请明确说『SKU A1001 数量改为5件』或『把当前版本第二行改成5件』；"
         "追加可明确说『在当前草稿新增商品 A1002 1件』；"
         "删除商品或调整顺序请在草稿表单中核对修改。")


def _mask_quotes(text):
    from erp_store.intent_contract import scan_source

    return scan_source(text).command_text if text.strip() else text


def _row_number(text):
    if text.isascii() and text.isdigit():
        return int(text)
    total, digit = 0, None
    for char in text:
        if char in _DIGITS:
            # Adjacent Chinese digits (e.g. 一二) are not a documented ordinal.
            if digit is not None:
                raise ValueError(_HINT)
            digit = _DIGITS[char]
        else:
            total += (digit if digit is not None else 1) * {"十": 10, "百": 100}[char]
            digit = None
    number = total + (digit or 0)
    if not 1 <= number <= 100:
        raise ValueError(_HINT)
    # Only accept the normal spelling of ordinals to avoid guessing malformed ones.
    if number < 10:
        canonical = "零一二三四五六七八九"[number]
    elif number < 100:
        tens, ones = divmod(number, 10)
        canonical = ("" if tens == 1 else "零一二三四五六七八九"[tens]) + "十"
        canonical += "零一二三四五六七八九"[ones] if ones else ""
    else:
        canonical = "一百"
    if text != canonical:
        raise ValueError(_HINT)
    return number


def needs_display_reference(text):
    commands = _mask_quotes(text)
    for clause in re.finditer(r"[^，,。；;\n]+", commands):
        value = clause.group().strip()
        if re.match(_DISPLAY_ROW, value):
            return True
        match = re.match(_ROW, value)
        if match and not match.groupdict().get("current"):
            return True
    return False


def _target(match, before, *, reference=None, strict_references=False, allow_rows=True):
    if sku := match.groupdict().get("sku"):
        line = next((row for row in before if row["sku"] == sku), None)
        if line is None:
            raise ValueError("本轮指定 SKU 不在当前草稿中；请在表单中核对新增商品，草稿未保存")
        if number := match.groupdict().get("number"):
            if not allow_rows or not 1 <= _row_number(number) <= len(before):
                raise ValueError("请核对当前草稿行号和 SKU；草稿未保存")
            if before[_row_number(number) - 1]["sku"] != sku:
                raise ValueError("本轮指定行号与 SKU 不一致，请核对目标商品；草稿未保存")
        label = match.groupdict().get("label") or match.groupdict().get("label_ascii")
        if label:
            def normalize(value):
                return re.sub(r"[\s·・（）()]", "", value)

            if normalize(label) != normalize(line.get("name") or ""):
                raise ValueError("本轮商品名称与 SKU 不一致，请核对目标商品；草稿未保存")
        return sku
    if match.groupdict().get("number") is not None:
        if not allow_rows:
            raise ValueError("待创建请求没有草稿版本行号，请明确 SKU 或所选商品的件数")
        number = _row_number(match["number"])
        if match.groupdict().get("view"):
            if reference is None or not 1 <= number <= len(reference["items"]):
                raise ValueError("没有可核对的旧展示行号，请明确 SKU 或当前版本行号")
            sku = reference["items"][number - 1]["sku"]
            if sku not in {row["sku"] for row in before}:
                raise ValueError(f"旧展示的 SKU {sku} 已不在当前草稿中，请核对完整要求")
            return sku
        if not 1 <= number <= len(before):
            raise ValueError("本轮指定行号超出当前草稿范围；草稿未保存")
        if strict_references and not match.groupdict().get("current"):
            if reference is None or not 1 <= number <= len(reference["items"]):
                raise ValueError("请明确当前版本行号或 SKU，旧展示行号需先核对对应商品")
            old_sku, current_sku = reference["items"][number - 1]["sku"], before[number - 1]["sku"]
            if old_sku != current_sku:
                raise ValueError(f"第 {number} 行旧展示为 {old_sku}，当前版本为 {current_sku}；"
                                 "请明确 SKU 或当前版本行号，草稿未修改")
        return before[number - 1]["sku"]
    if match.groupdict().get("name") is not None:
        name = match['name'].strip()
        matches = [row for row in before if row.get('name') == name or row['sku'] == name]
        if len(matches) != 1:
            raise ValueError("商品名称在当前草稿中不唯一或不存在，请明确 SKU 或行号；草稿未保存")
        return matches[0]['sku']
    sku = match["sku"]
    if sku not in {line["sku"] for line in before}:
        raise ValueError("本轮指定 SKU 不在当前草稿中；请在表单中核对新增商品，草稿未保存")
    return sku


def _requirements(text, source, before, *, implicit_sku=None, reference=None,
                  strict_references=False, allow_rows=True):
    text = _mask_quotes(text)
    clauses = list(re.finditer(r"[^，,。；;\n]+", text))
    found = {}
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        value = clause.group().strip()
        selected, amount, end = None, None, clause.end()
        for target_pattern, change_pattern, preserve_pattern in _TARGETS:
            if match := change_pattern.fullmatch(value) or preserve_pattern.fullmatch(value):
                selected, amount = match, match["quantity"]
                break
            if (match := target_pattern.fullmatch(value)) and index + 1 < len(clauses):
                following = clauses[index + 1]
                # Only the adjacent comma-separated quantity can complete a target.
                if (re.fullmatch(r"[，,\s]+", text[clause.end():following.start()])
                        and (quantity := _QUANTITY.fullmatch(following.group().strip()))):
                    selected, amount, end = match, quantity["quantity"], following.end()
                    index += 1
                    break
        if match := _ADD.fullmatch(value):
            sku, amount = match["sku"], match["quantity"]
            if sku in {row["sku"] for row in before} or sku in found:
                raise ValueError("追加商品须使用当前草稿中不存在的唯一 SKU；草稿未保存")
            if index + 1 < len(clauses):
                following = clauses[index + 1]
                if position := _POSITION.fullmatch(following.group().strip()):
                    new_count = len(before) + sum(row["sku"] not in {x["sku"] for x in before}
                                                  for row in found.values()) + 1
                    if _row_number(position["number"]) != new_count:
                        raise ValueError("追加商品只支持放在末行；插入或重排行请在草稿表单核对")
                    index += 1
                    end = following.end()
        elif selected is not None:
            sku = _target(selected, before, reference=reference,
                          strict_references=strict_references, allow_rows=allow_rows)
            if amount is None:
                amount = next(row["quantity"] for row in before if row["sku"] == sku)
        elif match := _QUANTITY.fullmatch(value):
            if implicit_sku is not None:
                if implicit_sku not in {row["sku"] for row in before}:
                    raise ValueError("请先完成商品选择，再核对新增件数；草稿未保存")
                sku = implicit_sku
            elif len(before) == 1:
                sku = before[0]["sku"]
            else:
                raise ValueError("多商品草稿须明确 SKU 或行号，草稿未保存")
            amount = match["quantity"]
        else:
            if _UNSUPPORTED_EDIT.search(value):
                raise ValueError(_HINT)
            index += 1
            continue
        if _CONDITIONAL.search(text):
            raise ValueError("条件、否定、只读、示例或可选数量不能授权修改；"
                             "请明确当前要修改的 SKU 和数量")
        quantity = (int(amount) if str(amount).isascii() and str(amount).isdigit()
                    else chinese_quantity(str(amount)))
        if sku in found and found[sku]["quantity"] != quantity:
            raise ValueError("本轮对同一商品存在多个不同数量，请明确唯一值；草稿未保存")
        found[sku] = {"sku": sku, "quantity": quantity,
                      "quote": source[clause.start():end].strip()}
        index += 1
    return list(found.values())


def validate_item_changes(tool_name, arguments, previous, instruction_text, source_text):
    """Return trusted, serializable evidence; never silently repair model arguments."""
    observed = [{"sku": line["sku"], "quantity": line["quantity"], "name": line.get("name")}
                for line in previous["items"]]
    requirements = _requirements(instruction_text, source_text, observed)
    before = [{"sku": row['sku'], "quantity": row['quantity']} for row in observed]
    requested = {row["sku"]: row["quantity"] for row in requirements}
    expected = [{"sku": row["sku"], "quantity": requested.get(row["sku"], row["quantity"])}
                for row in before]
    expected.extend({"sku": row["sku"], "quantity": row["quantity"]} for row in requirements
                    if row["sku"] not in {line["sku"] for line in before})
    if tool_name == "revise_order_draft_quantity":
        after = [{**row, "quantity": arguments["quantity"]}
                 if i == arguments["item_number"] else dict(row)
                 for i, row in enumerate(before, 1)]
    else:
        # Match the domain's merge/strip behavior, including stable first-SKU order.
        merged = {}
        for row in arguments["items"]:
            sku, quantity = row["sku"].strip(), row["quantity"]
            if type(quantity) is not int or quantity < 1:
                raise ValueError(_HINT)
            merged[sku] = merged.get(sku, 0) + quantity
        after = [{"sku": sku, "quantity": quantity} for sku, quantity in merged.items()]
    if after != expected:
        raise ValueError(_HINT)
    return {"contract": "draft-items-1", "draft_id": previous["draft_id"],
            "revision": previous["revision"], "before": before, "after": after,
            "requirements": requirements}

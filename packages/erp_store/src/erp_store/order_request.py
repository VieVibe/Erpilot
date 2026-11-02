"""Bounded user order language and exact catalogue resolution; never saves a draft."""

import hashlib
import re

from sqlalchemy import func, or_, select

from erp_store.db import ProductRow

CONTRACT = "draft-create-names-1"
_CN = "零一二两三四五六七八九十百千"
_NUMBER = rf"(?:[0-9]+|[{_CN}]+)"
_HEADER = re.compile(
    r"\s*(?:请\s*)?(?:给|为)\s*(?P<customer>[^，,。；;:\n]{1,80}?)\s*"
    r"(?P<action>(?:保存|创建|新建|准备|生成)(?:一(?:份|个|张))?(?:报价|订单)?草稿|"
    r"开(?:一)?个草稿|准备订单)\s*[:：]"
)
_SUFFIX = re.compile(rf"(?P<product>.+?)\s*(?P<number>{_NUMBER})\s*件")
_PREFIX = re.compile(rf"(?P<number>{_NUMBER})\s*件\s*(?P<product>.+)")
_OTHER_UNIT = re.compile(
    rf"(?P<product>.+?)\s*(?P<number>{_NUMBER})\s*(?P<unit>箱|包|盒|套|组|台|个|只|支|瓶|袋)"
)
_NO_UNIT = re.compile(rf"(?P<product>.+?)\s+(?P<number>{_NUMBER})")
_TAIL = re.compile(
    r"按(?:此|这个)顺序|其他不变|只(?:存|保存)草稿|只保存|(?:先|暂时)?不提交(?:订单)?|"
    r"不要提交|备注.*|(?:预算(?:上限)?|总预算|总价)(?:\s|为|改|上限|[:：]).*"
)
_UNCERTAIN = re.compile(
    r"如果|假如|假设|要是|除非|一旦|否则|例如|举例|示例|倘若|若|只要|或者|或是|还是|"
    r"(?:等|待)[^，,。；;\n]{0,32}(?:确认|批准|同意)(?:后|再)|"
    r"(?:不要|别|禁止|不许|暂不|先不|不)(?:再|要)?(?:创建|保存|新建|生成|准备)|"
    r"只(?:查询|查看|解释|说明|分析|讨论)|(?:不要|排除|除外|不含|不包括)(?:商品|SKU)"
)
HINT = ("请明确客户、完整商品名称或 SKU，以及每项的件数，例如"
        "『给客户甲准备订单：无线鼠标两件，三件机械键盘；只保存草稿』。"
        "重名请选择明确 SKU 后重新列出本次完整清单；箱、包等换算单位需要先确认。")


def mask_quotes(text):
    from erp_store.intent_contract import scan_source

    return scan_source(text).command_text


def _chinese(number):
    digits = "零一二三四五六七八九"
    result, zero = "", False
    for unit, suffix in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, number = divmod(number, unit)
        if digit:
            if zero:
                result += "零"
            result += ("" if unit == 10 and digit == 1 and not result else digits[digit]) + suffix
            zero = False
        elif result and number:
            zero = True
    return result


def quantity(text):
    if re.fullmatch(r"[1-9][0-9]{0,8}", text):
        return int(text)
    normalized = text.replace("两千", "二千").replace("两百", "二百")
    if normalized == "两":
        normalized = "二"
    total, digit = 0, None
    for char in normalized:
        if char in "零一二三四五六七八九":
            digit = "零一二三四五六七八九".index(char)
        elif char in "十百千":
            total += (digit if digit is not None else 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = None
        else:
            raise ValueError("件数无法核验；" + HINT)
    number = total + (digit or 0)
    if not 1 <= number <= 9999 or _chinese(number) != normalized:
        raise ValueError("件数无法核验；" + HINT)
    return number


def parse_request(source, *, extended=False, allow_incomplete=False):
    if not isinstance(source, str) or not source.strip() or len(source) > 32000:
        raise ValueError(HINT)
    text = mask_quotes(source)
    header = _HEADER.match(text)
    if not header or _UNCERTAIN.search(text):
        raise ValueError("条件、否定、引文或未明确的清单需先澄清。" + HINT)
    customer = re.sub(r"^\s*(?:请\s*)?(?:给|为)\s*", "", source[:header.start('action')]).strip()
    for left, right in (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'), ("'", "'")):
        if customer.startswith(left) and customer.endswith(right):
            customer = customer[1:-1].strip()
            break
    if not customer:
        raise ValueError("请明确客户。" + HINT)
    requirements, tail = [], False
    for clause in re.finditer(r"[^，,、。；;\n]+", text[header.end():]):
        value = clause.group().strip()
        if not value:
            continue
        if (re.search(r"[0-9]+\.[0-9]+\s*件$", value)
                or re.search(r"(?:^|\s)[+-]\s*[0-9]+\s*件$", value)):
            raise ValueError("件数必须为明确正整数；" + HINT)
        prefix = _PREFIX.fullmatch(value)
        match = prefix or _SUFFIX.fullmatch(value)
        if match and not tail:
            product = match['product'].strip()
            kind = "sku" if product.startswith("SKU ") else "name_or_sku"
            if kind == "sku":
                product = product[4:].strip()
            if not product or len(product) > 128 or re.search(r"[:：<>\[\]{}]", product):
                raise ValueError(HINT)
            start = (header.end() + clause.start() + len(clause.group())
                     - len(clause.group().lstrip()))
            requirements.append({
                "product_query": product, "reference_kind": kind,
                "quantity": quantity(match['number']), "quote": source[start:start + len(value)],
                "quantity_position": "before" if prefix else "after",
                "quantity_text": match['number'],
            })
        elif (_TAIL.fullmatch(value) or (extended and re.fullmatch(
                r"(?:预算(?:上限)?|总价)(?:不超过|不得超过|最多).*", value))):
            tail = True
        elif allow_incomplete and not tail:
            partial = _OTHER_UNIT.fullmatch(value) or _NO_UNIT.fullmatch(value)
            if partial and partial['product'].strip() == "SKU":
                partial = None  # A numeric SKU is still a product identifier, not a count.
            product = partial['product'].strip() if partial else value
            kind = "sku" if product.startswith("SKU ") else "name_or_sku"
            if kind == "sku":
                product = product[4:].strip()
            if not product or len(product) > 128 or re.search(r"[:：<>\[\]{}]", product):
                raise ValueError(HINT)
            if partial:
                quantity(partial['number'])  # Reject malformed or nonpositive counts.
            start = header.end() + clause.start() + len(clause.group()) - len(
                clause.group().lstrip())
            requirements.append({
                "product_query": product, "reference_kind": kind, "quantity": None,
                "quote": source[start:start + len(value)], "quantity_position": "missing",
                "quantity_text": None,
            })
        else:
            raise ValueError("商品名、件数或单位不完整。" + HINT)
    if not 1 <= len(requirements) <= 100:
        raise ValueError(HINT)
    return {"customer": customer, "customer_quote": source[:header.end()].strip(),
            "requirements": requirements}


def needs_resolution(source):
    if not isinstance(source, str) or not _HEADER.match(mask_quotes(source)):
        return False
    try:
        parsed = parse_request(source)
    except ValueError:
        return True
    return ("准备订单" in source or "个草稿" in source or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", row['product_query'])
        or re.search(rf"[{_CN}]", row['quote']) for row in parsed['requirements']
    ))


def _alternative_queries(requirement):
    query, raw_number = requirement['product_query'], requirement['quantity_text']
    if requirement['quantity_position'] == "missing":
        literal = requirement['quote'].removeprefix("SKU ").strip()
        return [literal] if literal != query else []
    if (requirement['quantity_position'] != "after"
            or re.search(r"\s" + re.escape(raw_number) + r"\s*件$", requirement['quote'])):
        return []
    alternatives = []
    for split in range(1, len(raw_number)):
        try:
            quantity(raw_number[split:])
        except ValueError:
            continue
        alternatives.append(query + raw_number[:split])
    return alternatives


def resolve_request(session, source, *, selections=None, extended=False, allow_incomplete=False,
                    quantities=None):
    digest = hashlib.sha256(source.encode()).hexdigest()
    try:
        parsed = parse_request(source, extended=extended, allow_incomplete=allow_incomplete)
    except ValueError as exc:
        return {"status": "clarification_required", "source_user_sha256": digest,
                "items": [], "issues": [{"code": "incomplete_request", "message": str(exc)}]}
    alternative_queries = [_alternative_queries(r) for r in parsed['requirements']]
    catalogue = None
    # A single name needs only one bounded lookup and count. Ranking thousands
    # of identical names is slower there; batch lists, explicit SKUs and boundaries.
    if extended and (len(parsed['requirements']) > 1 or any(alternative_queries)
                     or parsed['requirements'][0]['reference_kind'] == "sku"):
        from erp_store.intent_catalog import ExactCatalog

        alternatives = {q for rows in alternative_queries for q in rows}
        catalogue = ExactCatalog(
            session, {r['product_query'] for r in parsed['requirements']} | alternatives,
            {r['product_query'] for r in parsed['requirements']
             if r['reference_kind'] != "sku"} | alternatives,
        )
    resolved, issues, seen = [], [], set()
    for index, requirement in enumerate(parsed['requirements'], 1):
        requirement = dict(requirement)
        if requirement['quantity'] is None and str(index) in (quantities or {}):
            count = quantities[str(index)]
            if type(count) is not int or not 1 <= count <= 999999999:
                raise ValueError("补充件数必须为明确正整数")
            requirement['quantity'] = count
        query = requirement['product_query']
        if catalogue is not None:
            total, matches = catalogue.lookup(
                query, sku_only=requirement['reference_kind'] == "sku")
        else:
            condition = ProductRow.sku == query
            if requirement['reference_kind'] != "sku":
                condition = or_(condition, ProductRow.name == query)
            total = session.scalar(select(func.count()).select_from(ProductRow).where(condition))
            matches = list(session.scalars(select(ProductRow).where(condition)
                                           .order_by(ProductRow.sku).limit(20)))
        candidates = [{"sku": p.sku, "name": p.name, "status": p.status} for p in matches]
        chosen = (selections or {}).get(str(index))
        if chosen is not None and total > 1 and total <= 20:
            selected = [p for p in matches if p.sku == chosen]
            if len(selected) == 1:
                matches, total = selected, 1
        alternatives = []
        # A catalogue name/SKU may end in numerals: B十二件 can mean B x12 or B十 x2.
        # Whitespace or putting the count first makes that boundary explicit.
        for alternative in alternative_queries[index - 1]:
            if catalogue is not None:
                _, rows = catalogue.lookup(alternative)
            else:
                rows = session.scalars(select(ProductRow).where(or_(
                    ProductRow.sku == alternative, ProductRow.name == alternative,
                )).limit(20))
            alternatives.extend({"sku": p.sku, "name": p.name, "status": p.status}
                                for p in rows)
        if alternatives:
            issues.append({"code": "quantity_boundary_ambiguous", "item_number": index,
                           "message": ("商品名与单位边界不明确，请提供明确 SKU 及最终件数。"
                                       if requirement['quantity_position'] == "missing" else
                                       "名称末尾数字与件数边界不明确，请把件数放在商品名前。"),
                           "candidates": candidates + alternatives})
        elif total != 1:
            if not total:
                # Suggestions are never an automatic match; escape LIKE metacharacters.
                suggestions = session.scalars(select(ProductRow).where(
                    ProductRow.name.contains(query, autoescape=True),
                ).order_by(ProductRow.sku).limit(20))
                candidates = [{"sku": p.sku, "name": p.name, "status": p.status}
                              for p in suggestions]
            issues.append({"code": "ambiguous_product" if total else "product_not_found",
                           "item_number": index, "product_query": query,
                           "message": "请确认完整商品名称或明确 SKU。", "candidates": candidates,
                           "exact_matches": total, "has_more": total > 20})
        elif matches[0].status != "在售":
            issues.append({"code": "product_unavailable", "item_number": index,
                           "product_query": query,
                           "message": "商品当前不在售，请明确选择其他商品。",
                           "candidates": candidates})
        elif matches[0].sku in seen:
            issues.append({"code": "duplicate_product", "item_number": index,
                           "message": "同一商品出现多次，请明确合并后的唯一件数。"})
        else:
            product = matches[0]
            seen.add(product.sku)
            resolved.append({**requirement, "sku": product.sku, "product_name": product.name})
        if requirement['quantity'] is None:
            issues.append({"code": "missing_quantity", "item_number": index,
                           "product_query": query, "candidates": [],
                           "message": ("请明确该项最终件数（正整数，单位为件）；"
                                       "不自动换算箱、包等单位。")})
    items = [{"sku": r['sku'], "quantity": r['quantity']} for r in resolved]
    guard = {"contract": CONTRACT, "items": items, "requirements": resolved}
    return {"status": "clarification_required" if issues else "resolved",
            "source_user_sha256": digest, "customer": parsed['customer'],
            "items": [] if issues else items, "issues": issues,
            "creation_guard": None if issues else guard, "next_action": HINT,
            **({"catalog_bindings": resolved} if allow_incomplete else {})}


def recheck_creation(session, evidence, snapshot):
    guard = evidence.get("creation_guard") if isinstance(evidence, dict) else None
    if not isinstance(guard, dict) or guard.get("contract") != CONTRACT:
        return
    from erp_store.mutations import MutationError

    source = evidence.get("source_user_text")
    actual = resolve_request(session, source) if isinstance(source, str) else {}
    if (actual.get("status") != "resolved" or actual['creation_guard'] != guard
            or actual['source_user_sha256'] != evidence.get("source_user_sha256")
            or actual['customer'] != snapshot['customer']
            or guard['items'] != [{"sku": r['sku'], "quantity": r['quantity']}
                                  for r in snapshot['items']]):
        raise MutationError("product_resolution_changed", "商品名称或清单已变化，草稿未保存",
                            "请重新核对完整商品名称、SKU 和件数")

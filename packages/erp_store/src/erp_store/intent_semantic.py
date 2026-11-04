"""Untrusted creation candidates: source checks and catalogue binding, never write permission."""

import json
import re
from dataclasses import asdict
from decimal import Decimal
from typing import Literal

from pydantic import Field
from sqlalchemy import select

from erp_store.db import ProductRow
from erp_store.intent_catalog import ExactCatalog
from erp_store.intent_contract import SourceMessage, SourceSpan, StrictModel, digest, scan_source
from erp_store.order_request import quantity

SEMANTIC_VERSION = "order-create-proposal-3"
SEMANTIC_PROMPT_VERSION = "order-create-proposal-prompt-6"

_NOTE_PREFIX = re.compile(
    r"(?:^|[，,。；;\n])\s*(?:请\s*)?(?:把|将|修改|调整|更新)?(?P<label>备注)(?:原文|内容)?\s*"
    r"(?:修改为|调整为|更新为|设置为|改成|改为|设为|写成|写为|写|为|是|[:：])?\s*"
    r"(?P<quote>[『「“‘\"'])?\s*$"
)
_NOTE_QUOTES = {"『": "』", "「": "」", "“": "”", "‘": "’", '"': '"', "'": "'"}


def _note_field(message, note, scoped):
    """A real substring is not necessarily a user-assigned note field."""
    match = _NOTE_PREFIX.search(message.text[: note.evidence.start])
    if match is None or any(
        left <= match.start("label") < right for left, right in scoped.protected
    ):
        raise ValueError("备注候选必须来自用户明确的备注字段")
    opening = match["quote"]
    if opening:
        end = message.text.find(_NOTE_QUOTES[opening], note.evidence.start)
    else:
        clause_end = re.search(r"[，,。；;\n]", message.text[note.evidence.start :])
        end = note.evidence.start + clause_end.start() if clause_end else len(message.text)
        end = len(message.text[:end].rstrip())
    if end != note.evidence.end:
        raise ValueError("备注候选必须保留完整备注字段，不能选择其中一段")


class TextCandidate(StrictModel):
    value: str = Field(min_length=1, max_length=255)
    evidence: SourceSpan


class QuantityCandidate(StrictModel):
    value: int = Field(ge=1, le=999999999)
    evidence: SourceSpan


class BudgetCandidate(StrictModel):
    value: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    evidence: SourceSpan


class ItemCandidate(StrictModel):
    product: TextCandidate
    quantity: QuantityCandidate | None


class CreateProposal(StrictModel):
    schema_version: Literal[1]
    operation: Literal["create"]
    action: SourceSpan
    customer: TextCandidate | None
    note: TextCandidate | None
    budget_limit: BudgetCandidate | None
    items: list[ItemCandidate] = Field(min_length=1, max_length=100)
    unresolved: list[SourceSpan] = Field(max_length=100)


class ReviseProposal(CreateProposal):
    operation: Literal["revise"]
    items: list[ItemCandidate] = Field(max_length=100)


class QuoteEvidence(StrictModel):
    message_id: str
    quote: str = Field(min_length=1, max_length=32000)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class QuoteText(TextCandidate):
    evidence: QuoteEvidence


class QuoteQuantity(QuantityCandidate):
    evidence: QuoteEvidence


class QuoteBudget(BudgetCandidate):
    evidence: QuoteEvidence = Field(
        description=(
            "只引用金额和人民币单位，例如 60元；不要包含预算、预算上限等前缀，不可只写裸数字。"
        )
    )


class QuoteItem(ItemCandidate):
    product: QuoteText
    quantity: QuoteQuantity | None


class QuoteCreateProposal(CreateProposal):
    """Wire schema only; persisted candidates retain the original strict span schema."""

    action: QuoteEvidence
    customer: QuoteText | None
    note: QuoteText | None
    budget_limit: QuoteBudget | None
    items: list[QuoteItem] = Field(min_length=1, max_length=100)
    unresolved: list[QuoteEvidence] = Field(
        max_length=100,
        description=(
            "仅列出尚未映射的实际原文片段，不得与任何已提取字段或 action 重叠，"
            "不得放入整条消息。没有未解释片段时为 []；缺失数量等用字段 null 表示。"
        ),
    )


class QuoteReviseProposal(QuoteCreateProposal):
    operation: Literal["revise"]
    items: list[QuoteItem] = Field(max_length=100)


def locate_proposal(message, proposal, *, revision=False):
    """Locate unique exact quotes; never repair supplied offsets or choose a duplicate."""
    message = SourceMessage.model_validate(message)
    wire = (QuoteReviseProposal if revision else QuoteCreateProposal).model_validate(proposal)

    def locate(value):
        if isinstance(value, list):
            return [locate(v) for v in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"message_id", "quote", "start", "end"}:
            if value["message_id"] != message.message_id:
                raise ValueError("候选必须引用当前真实消息")
            start, end = value["start"], value["end"]
            if start is None and end is None:
                quote = value["quote"]
                start = message.text.find(quote)
                if start < 0 or message.text.find(quote, start + 1) >= 0:
                    raise ValueError("原文片段缺失或重复，请提供明确位置")
                end = start + len(quote)
            elif start is None or end is None:
                raise ValueError("来源起止位置必须同时提供")
            span = SourceSpan(**{**value, "start": start, "end": end})
            if not span.verify(message):
                raise ValueError("提供的来源位置不一致，不能自动修正")
            return span.model_dump(mode="json")
        return {k: locate(v) for k, v in value.items()}

    return validate_proposal(message, locate(wire.model_dump(mode="json")), revision=revision)


def semantic_eligible(source):
    scoped = scan_source(source)
    text = re.sub(r"不要提交|先别提交|不提交", "", scoped.command_text)
    return (
        not scoped.issues
        and bool(re.search(r"草稿|采购清单|订单", text))
        and bool(re.search(r"帮|给|建|准备|做|列|要", text))
        and not re.search(
            r"如果|假如|假设|除非|一旦|先别|不要|不创建|不保存|不买|"
            r"是否|能否|可以吗|怎么|如何|查询|只查|只看|解释|"
            r"修改|改成|改为|原草稿|已有草稿|现有草稿|当前版本|"
            r"这张草稿|第.*行|刚才|等.*确认",
            text,
        )
    )


def revision_eligible(source):
    scoped = scan_source(source)
    text = scoped.command_text
    return (
        not scoped.issues
        and bool(re.search(r"修改|调整|改|换成|更新", text))
        and not re.search(
            r"如果|假如|假设|除非|一旦|先别|不要|是否|能否|可以吗|"
            r"不改|不调整|不更新|不修改|不用|无需|不需要|没必要|"
            r"别改|别调整|别修改|勿改|禁止|能不能|要不要|可不可以|"
            r"怎么|如何|只查|只看|查询|解释|等.*确认|"
            r"提交|下单|建单|审批|取消|删除|移除|新增|刚才|上次|第.*行",
            text,
        )
    )


def validate_proposal(message, proposal, *, revision=False):
    message = SourceMessage.model_validate(message)
    proposal = (ReviseProposal if revision else CreateProposal).model_validate(proposal)
    if not (revision_eligible if revision else semantic_eligible)(message.text):
        raise ValueError("该作用域不能生成创建候选，请明确完整新请求")
    scoped = scan_source(message.text)
    if not re.match(
        r"修改|调整|改|换成|更新|把" if revision else r"准备|列|做|创建|建|帮|给|要",
        proposal.action.quote,
    ):
        raise ValueError("候选动作未指向实际动作片段")
    if revision and (
        not any((proposal.customer, proposal.note, proposal.budget_limit, proposal.items))
        or any(r.quantity is None for r in proposal.items)
    ):
        raise ValueError("修改候选必须有明确字段或最终件数，缺失不能清空原值")
    spans = [proposal.action, *proposal.unresolved]
    unquoted = [proposal.action]
    for field in (proposal.customer, proposal.note):
        if field:
            if field.value != field.evidence.quote:
                raise ValueError("客户与备注必须保留实际原文")
            spans.append(field.evidence)
    if proposal.customer and len(proposal.customer.value) > 64:
        raise ValueError("客户名超限")
    for row in proposal.items:
        if row.product.value != row.product.evidence.quote or len(row.product.value) > 128:
            raise ValueError("商品必须来自原文中的完整名称或 SKU")
        spans.append(row.product.evidence)
        unquoted.append(row.product.evidence)
        if row.quantity:
            count = re.fullmatch(
                r"([1-9][0-9]{0,8}|[零一二两三四五六七八九十百千]+)\s*[件个]",
                row.quantity.evidence.quote,
            )
            if not count or quantity(count[1]) != row.quantity.value:
                raise ValueError("候选件数不能推测包装换算或改写数值")
            spans.append(row.quantity.evidence)
            unquoted.append(row.quantity.evidence)
    if proposal.budget_limit:
        budget = proposal.budget_limit
        literal = re.fullmatch(
            r"(?:[¥￥]\s*)?([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|人民币)?", budget.evidence.quote
        )
        if (
            not literal
            or not re.search(r"元|人民币|[¥￥]", budget.evidence.quote)
            or Decimal(literal[1]) != Decimal(budget.value)
            or not 0 < Decimal(budget.value) <= Decimal("1000000000000")
        ):
            raise ValueError("预算候选必须保留人民币原始金额")
        spans.append(budget.evidence)
        unquoted.append(budget.evidence)
    if any(not span.verify(message) for span in spans):
        raise ValueError("候选来源区间与当前真实消息不一致")
    if proposal.note:
        _note_field(message, proposal.note, scoped)
    if any(a.start < end and a.end > start for a in unquoted for start, end in scoped.protected):
        raise ValueError("引文中的商品、数量或动作不能变成创建候选")
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    if any(a.end > b.start for a, b in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("同一来源片段不能重复分配给多个候选字段")
    return proposal


def bind_proposal(session, message, proposal):
    proposal = validate_proposal(message, proposal)
    queries = {r.product.value for r in proposal.items}
    catalogue = ExactCatalog(session, queries, queries)
    items, facts, skus = [], [], set()
    for index, row in enumerate(proposal.items, 1):
        query = row.product.value
        total, matches = catalogue.lookup(query)
        if not total or total > 20:
            raise ValueError("商品候选无法完整核对，请明确完整商品名或 SKU")
        facts.append([query, total, [asdict(p) for p in matches]])
        skus.update(p.sku for p in matches)
        product = matches[0] if total == 1 else None
        items.append(
            {
                "item_number": index,
                "product_query": query,
                "sku": product.sku if product else None,
                "name": product.name if product else None,
                "quantity": row.quantity.value if row.quantity else None,
                "candidates": [{"sku": p.sku, "name": p.name, "status": p.status} for p in matches]
                if total > 1
                else [],
            }
        )
    fields = {
        "customer": proposal.customer.value if proposal.customer else "",
        "note": proposal.note.value if proposal.note else None,
        "budget_limit": float(proposal.budget_limit.value) if proposal.budget_limit else None,
        "items": items,
    }
    facts.append(
        [
            [p.sku, p.name, p.status, p.price, p.category]
            for p in session.scalars(
                select(ProductRow).where(ProductRow.sku.in_(skus)).order_by(ProductRow.sku)
            )
        ]
    )
    return fields, digest(json.dumps(facts, ensure_ascii=False, sort_keys=True))


def recheck_semantic(session, data):
    if data.get("semantic_preview_version") != 1 or len(data["messages"]) != 1:
        raise ValueError("候选来源格式已变化")
    fields, fingerprint = bind_proposal(session, data["messages"][0], data["semantic_proposal"])
    if fingerprint != data["semantic_catalogue_sha256"]:
        raise ValueError("候选商品目录已变化")
    return fields


def unmapped_text(message, proposal):
    proposal = (
        ReviseProposal if proposal.get("operation") == "revise" else CreateProposal
    ).model_validate(proposal)
    output = list(message["text"])
    spans = [proposal.action]
    for field in (proposal.customer, proposal.note, proposal.budget_limit):
        if field:
            spans.append(field.evidence)
    for row in proposal.items:
        spans.append(row.product.evidence)
        if row.quantity:
            spans.append(row.quantity.evidence)
    for span in spans:
        output[span.start : span.end] = " " * (span.end - span.start)
    return "".join(output).strip()

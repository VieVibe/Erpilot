"""Explicit target, immutable revision and exact source checks for editable revision previews."""

import json
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select

from erp_store.db import OrderDraftRow, ProductRow
from erp_store.intent_contract import digest
from erp_store.intent_rules import field_values
from erp_store.intent_semantic import validate_proposal

REVISION_SEMANTIC_VERSION = "order-revise-proposal-2"
REVISION_PROMPT_VERSION = "order-revise-proposal-prompt-3"


def revision_target(session, session_id, draft_id, revision, now):
    row = session.scalar(select(OrderDraftRow).where(
        OrderDraftRow.session_id == session_id, OrderDraftRow.draft_id == draft_id,
    ).order_by(OrderDraftRow.revision.desc()).limit(1))
    if (row is None or row.revision != revision or row.order_id
            or datetime.fromisoformat(row.expires_at) <= now):
        raise ValueError("目标草稿已变化、过期、提交或不属于当前会话")
    return field_values(json.loads(row.snapshot)), {
        "draft_id": row.draft_id, "revision": row.revision,
        "snapshot_sha256": digest(row.snapshot),
    }


def bind_revision(session, message, proposal, base):
    proposal = validate_proposal(message, proposal, revision=True)
    fields = deepcopy(base)
    skus = [r["sku"] for r in base["items"]]
    products = list(session.scalars(select(ProductRow).where(
        ProductRow.sku.in_(skus)).order_by(ProductRow.sku)))
    if len(products) != len(skus) or any(p.status != "在售" for p in products):
        raise ValueError("原清单商品目录已变化，请重新核对")
    changed = set()
    for item in proposal.items:
        matches = [p for p in products if item.product.value in (p.sku, p.name)]
        if len(matches) != 1 or matches[0].sku in changed:
            raise ValueError("请明确原草稿中唯一商品名或 SKU，不自动新增或合并商品")
        sku = matches[0].sku
        changed.add(sku)
        next(r for r in fields["items"] if r["sku"] == sku)["quantity"] = item.quantity.value
    for name in ("customer", "note", "budget_limit"):
        candidate = getattr(proposal, name)
        if candidate is not None:
            fields[name] = float(candidate.value) if name == "budget_limit" else candidate.value
    fingerprint = digest(json.dumps([[p.sku, p.name, p.price, p.status, p.category]
                                     for p in products], ensure_ascii=False))
    return fields, fingerprint


def recheck_revision(session, data, now):
    if data.get("semantic_operation") != "revise" or len(data["messages"]) != 1:
        raise ValueError("修改候选来源无效")
    target = data["semantic_target"]
    base, actual = revision_target(session, data["session_id"], target["draft_id"],
                                   target["revision"], now)
    if actual != target or base != data["before_fields"]:
        raise ValueError("目标草稿来源已变化")
    fields, fingerprint = bind_revision(session, data["messages"][0],
                                        data["semantic_proposal"], base)
    if fingerprint != data["semantic_catalogue_sha256"]:
        raise ValueError("商品目录已变化")
    return fields

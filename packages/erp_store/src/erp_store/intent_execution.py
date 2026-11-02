"""Reconstruct order intent from trusted sources and real catalogue facts."""

import json
from copy import deepcopy
from typing import Literal
from uuid import uuid4

from pydantic import Field
from sqlalchemy import select

from erp_store.db import OrderDraftRow, ProductRow
from erp_store.intent_continuation import (
    literal_origin,
    merge_creation_messages,
    parsed_creation,
    preserved_fields,
    replay_quantities,
    replay_selection_messages,
)
from erp_store.intent_contract import CONTRACT, SourceMessage, StrictModel, digest
from erp_store.intent_references import DraftReference, load_reference
from erp_store.intent_rules import analyze_rules, field_values
from erp_store.intent_state import PendingIntentRow, require_pending
from erp_store.mutations import MutationError
from erp_store.order_request import resolve_request


class PendingRef(StrictModel):
    pending_id: str
    state_version: int = Field(ge=1)


class TargetRef(StrictModel):
    draft_id: str
    revision: int = Field(ge=1)


class IntentEvidence(StrictModel):
    contract: Literal["draft-intent-2"] = CONTRACT
    session_id: str
    request_id: str
    messages: list[SourceMessage] = Field(min_length=1, max_length=102)
    pending: PendingRef | None = None
    target: TargetRef | None = None
    reference: DraftReference | None = None


class CatalogChanged(ValueError):
    """A displayed candidate dependency changed, so its old numbering is invalid."""


def catalog_fingerprint(session, resolution):
    skus = {r["sku"] for r in (resolution.get("creation_guard") or {}).get("requirements", [])}
    skus.update(r["sku"] for r in resolution.get("catalog_bindings", []))
    for issue in resolution.get("issues", []):
        skus.update(c["sku"] for c in issue.get("candidates", []))
    rows = session.scalars(select(ProductRow).where(ProductRow.sku.in_(skus))
                           .order_by(ProductRow.sku))
    return digest(json.dumps([[r.sku, r.name, r.status, r.price, r.category] for r in rows],
                             ensure_ascii=False))


def reconstruct_creation(session, data):
    source = data["messages"][0]["text"]
    incomplete = data.get("creation_slots_version") == 1
    actual = resolve_request(session, source, extended=True, allow_incomplete=incomplete)
    if (actual != data["initial_resolution"]
            or catalog_fingerprint(session, actual) != data["catalog_fingerprint"]):
        raise CatalogChanged("候选商品或清单已变化，请重新核对并展示候选")
    selections, unresolved = replay_selection_messages(data)
    resolution = resolve_request(session, source, selections=selections, extended=True,
                                 allow_incomplete=incomplete, quantities=replay_quantities(data))
    analyzed = merge_creation_messages(data, resolution)
    if unresolved or resolution["status"] != "resolved":
        resolution["preserved_fields"] = preserved_fields(data, analyzed)
        return resolution, None
    return resolution, analyzed


def verify_for_save(session, session_id, evidence, snapshot, previous, draft_id, revision, now):
    """Called under the ERP write lock, before audit/save/commit; consumes atomically.

    Only this replay produces an audit plan. No normalized or invented user
    message is created and no model-provided verification flag is accepted.
    """
    try:
        verified = IntentEvidence.model_validate(evidence)
        if verified.session_id != session_id:
            raise ValueError("请求不属于当前会话")
        messages = [m.model_dump() for m in verified.messages]
        pending = None
        if previous is None:
            if (verified.pending is None or verified.target is not None
                    or verified.reference is not None):
                raise ValueError("缺少待完成请求或目标类型错误")
            pending = require_pending(session, session_id, verified.pending.pending_id,
                                      verified.pending.state_version, now)
            data = json.loads(pending.payload)
            if data["messages"] != messages or data["session_id"] != session_id:
                raise ValueError("原始消息与持久化请求不一致")
            resolution, analyzed = reconstruct_creation(session, data)
            if analyzed is None:
                raise ValueError("仍有待解决的问题")
            frame, expected, literals, requirements = analyzed
            catalog_binding = resolution["creation_guard"]
        else:
            if (verified.pending is not None or verified.target is None or len(messages) != 1
                    or verified.target.draft_id != draft_id
                    or verified.target.revision != revision - 1):
                raise ValueError("目标草稿或版本不一致")
            observed = {**previous, "draft_id": draft_id, "revision": revision - 1}
            reference = load_reference(
                session, session_id, verified.reference, target_id=draft_id,
                current_revision=revision - 1,
            )
            frame, expected, literals, requirements = analyze_rules(
                messages[0]["text"], session_id=session_id, request_id=verified.request_id,
                previous=observed, reference=reference,
            )
            message = verified.messages[0]
            literals = {k: literal_origin(message, v) for k, v in literals.items()}
            requirements = [{"sku": r["sku"], "quantity": r["quantity"],
                             "evidence_refs": [{k: v for k, v in literal_origin(message, r).items()
                                                if k in {"message_id", "start", "end", "quote"}}]}
                            for r in requirements]
            catalog_binding = None
        if frame.decision != "ready" or expected != field_values(snapshot):
            raise ValueError("完整草稿与已核验要求不一致；未保存部分修改")
        plan = {"expected": expected, "fields": literals, "items": requirements}
        if pending:
            pending.status, pending.version = "consumed", pending.version + 1
        return plan, {"contract": CONTRACT, "replay_version": 2,
                        "request_id": verified.request_id,
                        "messages": deepcopy(messages), "parser_version": frame.parser_version,
                        "policy_version": frame.policy_version,
                        "catalog_binding": deepcopy(catalog_binding),
                        "reference": (verified.reference.model_dump()
                                      if verified.reference else None),
                        "pending": verified.pending.model_dump() if verified.pending else None}
    except (ValueError, KeyError, TypeError) as exc:
        raise MutationError("intent_evidence_invalid", str(exc), "请重新核对完整要求") from exc


def make_evidence(session_id, messages, *, pending=None, previous=None, reference=None):
    return IntentEvidence(session_id=session_id, request_id=uuid4().hex,
                          messages=[SourceMessage.model_validate(m) for m in messages],
                          pending=PendingRef(pending_id=pending["pending_id"],
                                             state_version=pending["state_version"])
                          if pending else None,
                          reference=reference,
                          target=TargetRef(draft_id=previous["draft_id"],
                                           revision=previous["revision"]) if previous else None
                          ).model_dump(mode="json")


def recheck_saved_intent(session, row, snapshot):
    """Read a v2 audit even after rollback to off mode; never consume it again.

    Catalogue/quote freshness remains the independent business review. A new
    same-name product after saving cannot reassign an already selected SKU.
    """
    record = snapshot.get("change_record", {})
    evidence = record.get("intent_evidence")
    if evidence is None:
        return
    try:
        if evidence.get("contract") != CONTRACT:
            raise ValueError("未知意图证据版本")
        messages = [SourceMessage.model_validate(m).model_dump() for m in evidence["messages"]]
        source = messages[0]["text"]
        if record.get("previous_revision") is None:
            ref = PendingRef.model_validate(evidence["pending"])
            pending = session.get(PendingIntentRow, ref.pending_id)
            if (pending is None or pending.session_id != row.session_id
                    or pending.status != "consumed" or pending.version != ref.state_version + 1):
                raise ValueError("已保存请求的事务消费记录不一致")
            data = json.loads(pending.payload)
            if data["messages"] != messages:
                raise ValueError("已保存请求的原始消息不一致")
            selections, remaining = replay_selection_messages(data)
            quantities = replay_quantities(data)
            parsed = parsed_creation(data)
            if remaining or len(parsed["requirements"]) != len(snapshot["items"]):
                raise ValueError("已保存清单不完整")
            if evidence.get("replay_version") == 2:
                binding = evidence["catalog_binding"]
                if (len(binding["requirements"]) != len(parsed["requirements"])
                        or binding["items"] != [{"sku": r["sku"], "quantity": r["quantity"]}
                                                for r in binding["requirements"]]):
                    raise ValueError("原始商品绑定不完整")
                for index, (requirement, bound, line) in enumerate(zip(
                    parsed["requirements"], binding["requirements"], snapshot["items"], strict=True,
                ), 1):
                    original = dict(requirement)
                    if original["quantity"] is None:
                        original["quantity"] = quantities.get(str(index))
                        if original["quantity"] is None:
                            raise ValueError("原始缺失件数没有补充来源")
                    if (any(bound.get(k) != v for k, v in original.items())
                            or bound["sku"] != line["sku"]
                            or bound["product_name"] != line["name"]
                            or requirement["product_query"] not in {
                                bound["sku"], bound["product_name"]}
                            or (str(index) in selections
                                and selections[str(index)] != bound["sku"])):
                        raise ValueError("已保存商品与原始绑定不一致")
                resolution = {"status": "resolved", "items": binding["items"],
                              "creation_guard": binding}
                frame, expected, _, _ = merge_creation_messages(data, resolution)
                if frame.decision != "ready" or expected != field_values(snapshot):
                    raise ValueError("已保存草稿与原始请求及补充要求不一致")
                return
            for index, (requirement, line) in enumerate(zip(
                parsed["requirements"], snapshot["items"], strict=True,
            ), 1):
                if (requirement["quantity"] != line["quantity"]
                        or requirement["product_query"] not in {line["sku"], line["name"]}
                        or (str(index) in selections and selections[str(index)] != line["sku"])):
                    raise ValueError("已保存商品与原始来源不一致")
            resolution = {"status": "resolved", "items": field_values(snapshot)["items"],
                          "creation_guard": record["creation_guard"]}
            previous = None
        else:
            if len(messages) != 1:
                raise ValueError("修改来源格式不一致")
            prior = session.get(OrderDraftRow, (row.draft_id, row.revision - 1))
            if prior is None or prior.session_id != row.session_id:
                raise ValueError("缺少原草稿版本")
            previous = {**json.loads(prior.snapshot), "draft_id": row.draft_id,
                        "revision": row.revision - 1}
            resolution = None
        reference = load_reference(session, row.session_id, evidence.get("reference"),
                                   target_id=row.draft_id, current_revision=row.revision - 1)
        frame, expected, _, _ = analyze_rules(source, session_id=row.session_id,
                                              request_id=evidence["request_id"],
                                              previous=previous, resolution=resolution,
                                              reference=reference)
        if frame.decision != "ready" or expected != field_values(snapshot):
            raise ValueError("已保存的完整草稿与来源不一致")
    except (ValueError, KeyError, TypeError) as exc:
        raise MutationError("intent_evidence_invalid", str(exc), "请重新核对草稿来源") from exc

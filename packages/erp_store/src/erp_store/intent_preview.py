"""Read-only editable request preview and atomic, explicit form confirmation."""

import json
from copy import deepcopy

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_store.db import ProductRow
from erp_store.intent_catalog import ExactCatalog
from erp_store.intent_continuation import parsed_creation, replay_selection_messages
from erp_store.intent_contract import StrictModel
from erp_store.intent_execution import reconstruct_creation
from erp_store.intent_rules import field_values
from erp_store.intent_state import require_pending
from erp_store.mutations import MutationError


class IntentPreviewRef(StrictModel):
    pending_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    state_version: int = Field(ge=1)


def request_preview(store, session_id):
    pending = store.current(session_id)
    if pending is None:
        return None
    data = {k: deepcopy(v) for k, v in pending.items() if k not in {
        "pending_id", "state_version", "status", "expires_at",
    }}
    fields = data["preserved_fields"]
    result = {"schema_version": 1, **{k: pending[k] for k in (
        "pending_id", "state_version", "expires_at", "status",
    )}, "can_confirm": False, "clarification_attempts": store.clarification_attempts(pending),
              "customer": fields.get("customer", ""), "note": fields.get("note"),
              "budget_limit": fields.get("budget_limit"), "items": [], "message": ""}
    try:
        with Session(store._engine) as session:
            if data.get("semantic_preview_version") == 1:
                from erp_store.intent_semantic import recheck_semantic, unmapped_text

                if data.get("semantic_operation") == "revise":
                    from erp_store.intent_revision import recheck_revision

                    fields = recheck_revision(session, data, store.now())
                    products = {p.sku: p for p in session.scalars(select(ProductRow).where(
                        ProductRow.sku.in_([r["sku"] for r in fields["items"]])))}
                    fields["items"] = [
                        {**row, "item_number": index,
                         "product_query": products[row["sku"]].name,
                         "name": products[row["sku"]].name, "candidates": []}
                        for index, row in enumerate(fields["items"], 1)]
                    result.update({"operation": "revise", "before_fields": data["before_fields"],
                                   "target": {k: data["semantic_target"][k]
                                              for k in ("draft_id", "revision")}})
                else:
                    fields = recheck_semantic(session, data)
                result.update(fields)
                result.update({"origin": "semantic_proposal",
                               "source_text": data["messages"][0]["text"],
                               "unmapped_text": unmapped_text(
                                   data["messages"][0], data["semantic_proposal"]),
                               "can_confirm": pending["status"] == "pending",
                               "message": "以下是待核对的候选字段。请对照完整原话检查全部要求，"
                                          "补齐或修改后按此完整清单保存草稿。"})
                if not result["can_confirm"]:
                    result["message"] = "待核对请求已过期，请重新说明完整要求。"
                return result
            resolution, analyzed = reconstruct_creation(session, data)
            fields = analyzed[1] if analyzed else resolution["preserved_fields"]
            parsed = parsed_creation(data)
            choices, _ = replay_selection_messages(data)
            names = {r["product_query"] for r in parsed["requirements"]
                     if r["reference_kind"] != "sku"}
            catalogue = ExactCatalog(session, {r["product_query"] for r in parsed["requirements"]},
                                     names)
            for index, row in enumerate(parsed["requirements"], 1):
                total, matches = catalogue.lookup(row["product_query"],
                                                  sku_only=row["reference_kind"] == "sku")
                sku = choices.get(str(index)) or (matches[0].sku if total == 1 else None)
                product = session.get(ProductRow, sku) if sku else None
                current = fields["items"][index - 1]
                result["items"].append({
                    "item_number": index, "product_query": row["product_query"],
                    "sku": sku, "name": product.name if product else None,
                    "quantity": current["quantity"],
                    "candidates": [{"sku": p.sku, "name": p.name, "status": p.status}
                                   for p in matches] if total > 1 else [],
                })
            result.update({k: fields[k] for k in ("customer", "note", "budget_limit")})
        result["can_confirm"] = pending["status"] in {"pending", "ready"}
        if not result["can_confirm"]:
            result["message"] = "待完成请求已过期，请重新说明完整要求。"
    except ValueError:
        result["status"] = "needs_recheck"
        result["message"] = "商品目录或原请求已变化，请重新说明完整要求后再核对。"
    return result


def consume_form_preview(session, session_id, ref, snapshot, draft_id, now, *,
                         previous=None, revision=1):
    """Invoked under the same write lock and transaction as the new draft version."""
    try:
        ref = IntentPreviewRef.model_validate(ref)
        row = require_pending(session, session_id, ref.pending_id, ref.state_version, now,
                              statuses=("pending", "ready"))
        data = json.loads(row.payload)
        if data.get("semantic_operation") == "revise":
            from erp_store.intent_revision import recheck_revision

            target = data["semantic_target"]
            if (previous is None or target["draft_id"] != draft_id
                    or target["revision"] != revision - 1):
                raise ValueError("不能将修改预览用于新建或其他草稿版本")
            recheck_revision(session, data, now)
        elif previous is not None:
            raise ValueError("创建预览不能用于修改")
        elif data.get("semantic_preview_version") == 1:
            from erp_store.intent_semantic import recheck_semantic

            recheck_semantic(session, data)
        else:
            reconstruct_creation(session, data)
        source = {"pending_id": ref.pending_id, "state_version": ref.state_version,
                  "messages": deepcopy(data["messages"])}
        if data.get("semantic_preview_version") == 1:
            source.update({"semantic_proposal": deepcopy(data["semantic_proposal"]),
                           "verification": "explicit_form",
                           "semantic_version": data["semantic_version"]})
        if data.get("semantic_operation") == "revise":
            source.update({"operation": "revise", "target": deepcopy(data["semantic_target"]),
                           "before_fields": deepcopy(data["before_fields"])})
        data["form_confirmation"] = {"draft_id": draft_id, "revision": revision,
                                     "fields": field_values(snapshot)}
        row.status, row.version = "consumed", row.version + 1
        row.payload = json.dumps(data, ensure_ascii=False)
        return source
    except ValueError as exc:
        raise MutationError("intent_preview_changed", "请求或商品目录已变化，"
                            "请重新核对完整请求") from exc

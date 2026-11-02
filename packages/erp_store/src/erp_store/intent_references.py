"""Bind a displayed draft observation to an immutable, session-owned revision."""

import json

from pydantic import Field

from erp_store.db import OrderDraftRow
from erp_store.intent_contract import StrictModel, digest
from erp_store.intent_rules import field_values

_DISPLAY_TOOLS = {"get_order_task", "get_order_draft", "prepare_order_draft",
                  "revise_order_draft", "revise_order_draft_quantity"}


class DraftReference(StrictModel):
    draft_id: str
    revision: int = Field(ge=1)
    observed_call_id: str = Field(min_length=1)
    snapshot_sha256: str


def snapshot_digest(snapshot):
    return digest(json.dumps(field_values(snapshot), ensure_ascii=False, sort_keys=True))


def previous_display(messages, target_id):
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
    names, observed = {}, None
    for message in messages[:last_user]:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                names[call.get("id")] = call.get("function", {}).get("name")
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            name = names.get(call_id)
            if name not in _DISPLAY_TOOLS:
                continue
            try:
                result = json.loads(message["content"])
                draft = result.get("draft") if name == "get_order_task" else result
                if (not isinstance(draft, dict) or not draft.get("draft_id")
                        or type(draft.get("revision")) is not int):
                    continue
                observed = DraftReference(draft_id=draft["draft_id"], revision=draft["revision"],
                                           observed_call_id=call_id,
                                           snapshot_sha256=snapshot_digest(draft))
            except (ValueError, TypeError, KeyError):
                continue
    return observed if observed and observed.draft_id == target_id else None


def load_reference(session, session_id, reference, *, target_id, current_revision):
    if reference is None:
        return None
    ref = DraftReference.model_validate(reference)
    row = session.get(OrderDraftRow, (ref.draft_id, ref.revision))
    if (row is None or row.session_id != session_id or ref.draft_id != target_id
            or ref.revision > current_revision):
        raise ValueError("旧展示不属于当前会话目标或版本，请重新核对 SKU")
    snapshot = json.loads(row.snapshot)
    if snapshot_digest(snapshot) != ref.snapshot_sha256:
        raise ValueError("旧展示与实际保存版本不一致，请重新核对 SKU")
    return {**snapshot, "draft_id": row.draft_id, "revision": row.revision}

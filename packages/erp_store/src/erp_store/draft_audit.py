"""Immutable constraint origins and server-computed quote changes.

These records live inside each draft revision's snapshot, so revision, origins and
diff commit together. Application evidence is never a model-visible tool argument.
"""

import hashlib
from copy import deepcopy

from erp_store.draft_budget import with_budget_origin
from erp_store.mutations import MutationError

FIELDS = ("customer", "note")
_LINE_FIELDS = (
    "name", "quantity", "base_price", "discount", "unit_price", "line_total",
    "stock_before", "stock_after",
)


def _invalid():
    return MutationError("constraint_evidence_invalid", "约束来源无法核对，草稿未保存")


def _source(evidence, snapshot, previous, draft_id, revision):
    if evidence is None:
        return {"kind": "unattributed"}, {}
    if not isinstance(evidence, dict):
        raise _invalid()
    if evidence == {"contract": "draft-form-1"}:
        return {"kind": "manual_form"}, {
            field: {"kind": "manual_form", "value": snapshot[field]}
            for field in (*FIELDS, "budget_limit") if field in snapshot
        }
    if evidence.get("contract") != "draft-literals-1":
        raise _invalid()
    text, digest = evidence.get("source_user_text"), evidence.get("source_user_sha256")
    if (
        not isinstance(text, str) or not text.strip() or len(text) > 32000
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest
        or not isinstance(evidence.get("fields"), dict)
        or set(evidence["fields"]) - {*FIELDS, "budget_limit"}
    ):
        raise _invalid()
    explicit = {}
    for field, entry in evidence["fields"].items():
        if not isinstance(entry, dict) or "value" not in entry or entry["value"] != snapshot[field]:
            raise _invalid()
        if entry.get("kind") == "user_literal":
            quote = entry.get("quote")
            if not isinstance(quote, str) or not quote or quote not in text:
                raise _invalid()
            explicit[field] = {
                "kind": "user_literal", "quote": quote, "source_user_sha256": digest,
                "value": snapshot[field],
            }
        elif entry.get("kind") == "observed_draft":
            if (
                previous is None or previous.get(field) != snapshot[field]
                or entry.get("draft_id") != draft_id or entry.get("revision") != revision - 1
            ):
                raise _invalid()
        else:
            raise _invalid()
    return {"kind": "agent", "user_request": text, "source_user_sha256": digest}, explicit


def snapshot_constraints(snapshot, revision):
    """Legacy snapshots keep an honest unknown source; no backfill is invented."""
    if "task_constraints" not in snapshot:
        return {
            "schema_version": 1,
            "fields": {
                field: {"value": snapshot[field], "verified": False,
                        "origin": {"kind": "legacy_snapshot", "revision": revision}}
                for field in FIELDS
            },
            "unverified_fields": [*FIELDS, "items"],
        }
    ledger = snapshot["task_constraints"]
    if (
        not isinstance(ledger, dict) or ledger.get("schema_version") != 1
        or not isinstance(ledger.get("fields"), dict) or set(ledger["fields"]) != set(FIELDS)
    ):
        raise _invalid()
    for field, entry in ledger["fields"].items():
        if (
            not isinstance(entry, dict) or "value" not in entry or entry["value"] != snapshot[field]
            or type(entry.get("verified")) is not bool or not isinstance(entry.get("origin"), dict)
        ):
            raise _invalid()
    return deepcopy(ledger)


def quote_changes(previous, snapshot):
    changes = []
    for field in (*FIELDS, "total_amount", "budget_limit"):
        before = previous.get(field) if previous is not None else None
        after = snapshot.get(field)
        if (previous is None and field != "budget_limit") or before != after:
            changes.append({"field": field, "before": before, "after": after})
    old = {line["sku"]: line for line in previous["items"]} if previous else {}
    new = {line["sku"]: line for line in snapshot["items"]}
    for sku in dict.fromkeys([*old, *new]):
        if sku not in old or sku not in new:
            changes.append({"field": "item", "sku": sku, "before": old.get(sku),
                            "after": new.get(sku)})
        else:
            for field in _LINE_FIELDS:
                if old[sku][field] != new[sku][field]:
                    changes.append({"field": field, "sku": sku,
                                    "before": old[sku][field], "after": new[sku][field]})
    if previous is not None and list(old) != list(new):
        changes.append({"field": "item_order", "before": list(old), "after": list(new)})
    return changes


def with_draft_audit(snapshot, *, draft_id, revision, operation, evidence=None, previous=None):
    source, explicit = _source(evidence, snapshot, previous, draft_id, revision)
    snapshot = with_budget_origin(snapshot, previous, explicit, revision)
    old = snapshot_constraints(previous, revision - 1)["fields"] if previous is not None else {}
    fields = {}
    for field in FIELDS:
        prior = old.get(field)
        if prior and prior["value"] == snapshot[field] and prior["verified"]:
            fields[field] = deepcopy(prior)
        elif field in explicit:
            fields[field] = {
                "value": snapshot[field], "verified": True,
                "origin": {**explicit[field], "revision": revision},
            }
        elif prior and prior["value"] == snapshot[field]:
            fields[field] = deepcopy(prior)
        else:
            if prior and prior["verified"]:
                raise MutationError(
                    "constraint_changed", "客户或备注与已保存要求不一致，草稿未修改",
                    "请明确新的要求并通过对话核对，或在草稿表单中明确修改",
                )
            fields[field] = {
                "value": snapshot[field], "verified": False,
                "origin": {"kind": "unverified", "revision": revision},
            }
    return {
        **snapshot,
        "task_constraints": {
            "schema_version": 1, "fields": fields,
            "unverified_fields": [field for field in FIELDS if not fields[field]["verified"]]
            + ["items"],
        },
        "change_record": {
            "schema_version": 1, "revision": revision,
            "previous_revision": revision - 1 if previous is not None else None,
            "operation": operation, "source": source,
            "confirmed_fields": list(explicit), "changes": quote_changes(previous, snapshot),
        },
    }

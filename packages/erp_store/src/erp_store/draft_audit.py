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


def _item_values(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise _invalid()
    for item in items:
        if (
            not isinstance(item, dict) or set(item) != {"sku", "quantity"}
            or not isinstance(item["sku"], str) or not item["sku"]
            or type(item["quantity"]) is not int or item["quantity"] < 1
        ):
            raise _invalid()
    if len({item["sku"] for item in items}) != len(items):
        raise _invalid()
    return items


def _creation_guard(evidence, snapshot, previous):
    if evidence is None or "creation_guard" not in evidence:
        return None
    guard = evidence["creation_guard"]
    if (previous is not None or evidence.get("contract") != "draft-literals-1"
            or not isinstance(guard, dict)
            or set(guard) != {"contract", "items", "requirements"}
            or guard.get("contract") not in {"draft-create-items-1", "draft-create-names-1"}):
        raise _invalid()
    items = _item_values(guard["items"])
    actual = [{"sku": row["sku"], "quantity": row["quantity"]} for row in snapshot["items"]]
    requirements = guard["requirements"]
    if items != actual or not isinstance(requirements, list) or len(requirements) != len(items):
        raise _invalid()
    for item, requirement in zip(items, requirements, strict=True):
        expected_keys = {"sku", "quantity", "quote"}
        if guard['contract'] == "draft-create-names-1":
            expected_keys |= {"product_query", "product_name", "reference_kind",
                              "quantity_position", "quantity_text"}
        if (not isinstance(requirement, dict)
                or set(requirement) != expected_keys
                or type(requirement["quantity"]) is not int
                or {key: requirement[key] for key in ("sku", "quantity")} != item
                or not isinstance(requirement["quote"], str) or not requirement["quote"]
                or requirement["quote"] not in evidence["source_user_text"]):
            raise _invalid()
    return deepcopy(guard)


def _item_constraint(snapshot, previous, evidence, creation_guard, item_guard, revision):
    value = [{"sku": row["sku"], "quantity": row["quantity"]} for row in snapshot["items"]]
    prior = previous.get("item_constraint") if previous else None
    if creation_guard:
        kind = ("catalog_resolved" if creation_guard['contract'] == "draft-create-names-1"
                else "user_literal")
        origin = {"kind": kind, "revision": revision,
                  "source_user_sha256": evidence["source_user_sha256"],
                  "requirements": creation_guard["requirements"]}
    elif evidence == {"contract": "draft-form-1"}:
        origin = {"kind": "manual_form", "revision": revision}
    elif prior and prior["value"] == value:
        return deepcopy(prior)
    elif prior and prior["verified"] and item_guard:
        origin = {"kind": "user_literal", "revision": revision,
                  "source_user_sha256": evidence["source_user_sha256"],
                  "requirements": item_guard["requirements"],
                  "previous_revision": revision - 1}
    else:
        origin = {"kind": "unverified", "revision": revision}
    return {"value": value, "verified": origin["kind"] != "unverified", "origin": origin}


def _item_guard(evidence, snapshot, previous, draft_id, revision):
    """Bind optional runtime checks to the actual snapshots in this transaction."""
    if evidence is None or "item_guard" not in evidence:
        return None
    guard = evidence["item_guard"]
    if (
        previous is None or evidence.get("contract") != "draft-literals-1"
        or not isinstance(guard, dict)
        or set(guard) != {"contract", "draft_id", "revision", "before", "after", "requirements"}
        or guard.get("contract") != "draft-items-1" or guard.get("draft_id") != draft_id
        or type(guard.get("revision")) is not int or guard["revision"] != revision - 1
    ):
        raise _invalid()
    before, after = _item_values(guard["before"]), _item_values(guard["after"])
    actual_before = [{"sku": line["sku"], "quantity": line["quantity"]}
                     for line in previous["items"]]
    actual_after = [{"sku": line["sku"], "quantity": line["quantity"]}
                    for line in snapshot["items"]]
    requirements = guard["requirements"]
    if (
        before != actual_before or after != actual_after
        or [item["sku"] for item in before] != [item["sku"] for item in after[:len(before)]]
        or not isinstance(requirements, list) or len(requirements) > 100
    ):
        raise _invalid()
    authorized = {}
    current = {item["sku"]: item["quantity"] for item in after}
    for requirement in requirements:
        if (
            not isinstance(requirement, dict) or set(requirement) != {"sku", "quantity", "quote"}
            or not isinstance(requirement["sku"], str) or requirement["sku"] not in current
            or requirement["sku"] in authorized or type(requirement["quantity"]) is not int
            or requirement["quantity"] < 1
            or requirement["quantity"] != current[requirement["sku"]]
            or not isinstance(requirement["quote"], str) or not requirement["quote"]
            or requirement["quote"] not in evidence["source_user_text"]
        ):
            raise _invalid()
        authorized[requirement["sku"]] = requirement["quantity"]
    prior = {item["sku"]: item["quantity"] for item in before}
    if any(prior.get(item["sku"]) != item["quantity"] and item["sku"] not in authorized
           for item in after):
        raise _invalid()
    return deepcopy(guard)


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
    if "item_constraint" in snapshot:
        entry = snapshot["item_constraint"]
        if (not isinstance(entry, dict) or type(entry.get("verified")) is not bool
                or not isinstance(entry.get("origin"), dict)
                or _item_values(entry.get("value")) != [
                    {"sku": row["sku"], "quantity": row["quantity"]} for row in snapshot["items"]
                ] or ("items" in ledger.get("unverified_fields", [])) == entry["verified"]):
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
    creation_guard = _creation_guard(evidence, snapshot, previous)
    item_guard = _item_guard(evidence, snapshot, previous, draft_id, revision)
    item_constraint = _item_constraint(
        snapshot, previous, evidence, creation_guard, item_guard, revision,
    )
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
        "item_constraint": item_constraint,
        "task_constraints": {
            "schema_version": 1, "fields": fields,
            "unverified_fields": [field for field in FIELDS if not fields[field]["verified"]]
            + ([] if item_constraint["verified"] else ["items"]),
        },
        "change_record": {
            "schema_version": 1, "revision": revision,
            "previous_revision": revision - 1 if previous is not None else None,
            "operation": operation, "source": source,
            "confirmed_fields": list(explicit), "changes": quote_changes(previous, snapshot),
            **({"item_guard": item_guard} if item_guard is not None else {}),
            **({"creation_guard": creation_guard} if creation_guard is not None else {}),
        },
    }

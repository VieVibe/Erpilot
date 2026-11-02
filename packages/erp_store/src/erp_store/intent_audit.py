"""Format an already replayed intent plan inside the caller's ERP transaction."""

from copy import deepcopy

from erp_store.draft_audit import FIELDS, quote_changes, snapshot_constraints
from erp_store.draft_budget import with_budget_origin
from erp_store.intent_rules import field_values


def with_intent_audit(snapshot, *, draft_id, revision, operation, plan, record, previous=None):
    explicit = plan["fields"]
    snapshot = with_budget_origin(snapshot, previous, explicit, revision)
    old = snapshot_constraints(previous, revision - 1)["fields"] if previous else {}
    fields = {}
    for field in FIELDS:
        prior = old.get(field)
        if prior and prior["value"] == snapshot[field] and prior["verified"]:
            fields[field] = deepcopy(prior)
        elif field in explicit:
            fields[field] = {"value": snapshot[field], "verified": True,
                             "origin": {**deepcopy(explicit[field]), "revision": revision}}
        elif prior and prior["value"] == snapshot[field]:
            fields[field] = deepcopy(prior)
        else:
            fields[field] = {"value": snapshot[field], "verified": False,
                             "origin": {"kind": "unverified", "revision": revision}}
    value = field_values(snapshot)["items"]
    prior_items = previous.get("item_constraint") if previous else None
    if prior_items and prior_items["value"] == value:
        item_constraint = deepcopy(prior_items)
    else:
        verified = previous is None or bool(prior_items and prior_items["verified"])
        item_constraint = {
            "value": value, "verified": verified,
            "origin": {"kind": "intent_verified" if verified else "unverified",
                       "revision": revision, "contract": record["contract"],
                       **({"previous_revision": revision - 1} if previous else {}),
                       "requirements": deepcopy(plan["items"]),
                       "source_messages": [{"message_id": m["message_id"], "sha256": m["sha256"]}
                                           for m in record["messages"]]},
        }
    original = record["messages"][0]
    return {
        **snapshot, "item_constraint": item_constraint,
        "task_constraints": {
            "schema_version": 1, "fields": fields,
            "unverified_fields": [k for k in FIELDS if not fields[k]["verified"]]
            + ([] if item_constraint["verified"] else ["items"]),
        },
        "change_record": {
            "schema_version": 1, "revision": revision,
            "previous_revision": revision - 1 if previous else None,
            "operation": operation,
            "source": {"kind": "agent", "contract": record["contract"],
                       "user_request": original["text"], "source_user_sha256": original["sha256"]},
            "confirmed_fields": list(explicit), "changes": quote_changes(previous, snapshot),
            "intent_evidence": deepcopy(record), "intent_plan": deepcopy(plan),
        },
    }

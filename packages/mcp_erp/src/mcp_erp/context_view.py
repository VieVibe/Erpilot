"""Historical draft summaries for the opt-in context experiment.

They are observations, never current inventory, authorization or execution facts.
The existing tool guidance still requires a fresh get_order_task before mutation.
"""

import json

from agent_core.context_view import ContextView


def summarize_task(content: str) -> str | None:
    try:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            return None
        if payload.get("status") not in {"active", "ambiguous", "none"}:
            return None
        result = {"status": payload["status"]}
        if payload["status"] == "active":
            draft = payload["draft"]
            result["draft"] = {
                key: draft[key] for key in
                ("draft_id", "revision", "customer", "note", "status", "expires_at")
            }
            result["draft"]["items"] = [
                {"sku": item["sku"], "quantity": item["quantity"]}
                for item in draft["items"]
            ]
        elif payload["status"] == "ambiguous":
            # Candidate identities and ambiguity must remain intact.
            result["candidates"] = payload["candidates"]
            result["has_more"] = payload["has_more"]
        result["context_view"] = "historical_task_snapshot"
        result["refresh_required"] = True
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except (ValueError, KeyError, TypeError):
        return None


def erp_context_view() -> ContextView:
    return ContextView(projectors={"get_order_task": summarize_task},
                       remember_tools=("get_order_task",))

import copy
import json
from pathlib import Path

import pytest
from agent_core.display import build_display

RESULT = json.loads((Path(__file__).resolve().parents[3]
                     / "apps/web/tests/replenishment-fixture.json").read_text(encoding="utf-8"))
ARGS = {"skus": ["B6-A"], "lead_time_days": 7}


def display(payload, args=None):
    return build_display("suggest_replenishment", args or ARGS, json.dumps(payload), True)


def test_valid_suggestion_uses_backend_facts():
    result = display(RESULT)
    assert result["kind"] == "replenishment_suggestion"
    assert result["outcome"] == "succeeded"
    assert result["suggestion"] == RESULT


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 2),
    (("assessed_at",), "2026-10-17T12:00:00"), (("window_start",), "2026-09-18T12:00:00+08:00"),
    (("days",), 7), (("coverage_days",), 0), (("lead_time_days",), 8),
    (("returned",), 0), (("has_more",), True), (("assumptions",), []),
    (("items", 0, "stock_quantity"), -1), (("items", 0, "sold_quantity"), True),
    (("items", 0, "daily_sales"), float("inf")),
    (("items", 0, "estimated_days_remaining"), 100),
    (("items", 0, "target_quantity"), 43), (("items", 0, "suggested_quantity"), 999),
    (("items", 0, "stockout_before_arrival"), None),
    (("items", 0, "decision"), "sufficient"), (("items", 0, "sku"), "wrong"),
])
def test_inconsistent_or_invalid_fields_fall_back(path, value):
    payload = copy.deepcopy(RESULT)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = display(payload)
    assert result is None or result["outcome"] == "unknown"
    assert not result or "suggestion" not in result


def test_unknown_lead_and_unavailable_quantity_remain_null():
    payload = copy.deepcopy(RESULT)
    payload["lead_time_days"] = None
    payload["items"][0].update(target_quantity=28, suggested_quantity=23,
                               stockout_before_arrival=None)
    assert display(payload, {"skus": ["B6-A"]})["outcome"] == "succeeded"
    payload["items"][0].update(sold_quantity=0, daily_sales=0, decision="no_sales",
                               estimated_days_remaining=None, target_quantity=None,
                               suggested_quantity=None)
    assert display(payload, {"skus": ["B6-A"]})["outcome"] == "succeeded"
    payload["items"][0]["suggested_quantity"] = 0
    assert display(payload, {"skus": ["B6-A"]})["outcome"] == "unknown"

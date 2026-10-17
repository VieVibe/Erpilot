import copy
import json

import pytest
from agent_core.display import build_display

RESULT = {
    "schema_version": 1,
    "query": "退货",
    "queried_at": "2026-10-17T08:00:00Z",
    "policy_state": "matched",
    "note": "",
    "matches": [
        {
            "source": "return-policy.md",
            "title": "七天无理由退货",
            "text": "签收后7天内可申请退货。",
            "score": 0.85,
            "policy_id": "return",
            "version": "2026-10-12.1",
            "effective_from": "2026-10-11T16:00:00Z",
            "effective_until": None,
        }
    ],
}


def display(result, args=None):
    return build_display("search_policy", args or {"query": " 退货 "}, json.dumps(result), True)


def test_search_preserves_exact_evidence_and_query():
    assert display(RESULT) == {
        "version": 1,
        "kind": "policy_search",
        "outcome": "succeeded",
        "query": "退货",
        "search": RESULT,
    }


@pytest.mark.parametrize("state", ["no_match", "inactive", "conflict", "unavailable"])
def test_missing_evidence_is_a_completed_query(state):
    result = {**RESULT, "policy_state": state, "matches": [], "note": "没有可引用依据"}
    assert display(result)["outcome"] == "succeeded"
    assert display(result)["search"]["matches"] == []


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("schema_version",), 2),
        (("query",), "折扣"),
        (("queried_at",), "2026-10-17T08:00:00"),
        (("policy_state",), "no_match"),
        (("matches",), []),
        (("note",), None),
        (("matches", 0, "score"), True),
        (("matches", 0, "score"), float("nan")),
        (("matches", 0, "score"), 1.01),
        (("matches", 0, "source"), ""),
        (("matches", 0, "effective_from"), "2027-01-01T00:00:00Z"),
        (("matches", 0, "effective_until"), RESULT["queried_at"]),
    ],
)
def test_invalid_search_cannot_be_presented_as_evidence(path, value):
    result = copy.deepcopy(RESULT)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert display(result)["outcome"] == "unknown"


def test_overlapping_versions_and_more_than_requested_results_are_rejected():
    result = copy.deepcopy(RESULT)
    result["matches"].append({**result["matches"][0], "version": "different"})
    assert display(result)["outcome"] == "unknown"
    result["matches"][1]["version"] = "2026-10-12.1"
    assert display(result, {"query": "退货", "k": 1})["outcome"] == "unknown"


def test_unavailable_error_keeps_query_and_safe_failure_message():
    projected = display({"error": {"code": "policy_unavailable", "message": "政策检索暂不可用"}})
    assert projected["query"] == "退货" and projected["outcome"] == "failed"
    assert "search" not in projected
    # Old results have no recorded query time: retain raw detail rather than inventing it.
    assert display({"matches": RESULT["matches"], "note": ""})["outcome"] == "unknown"

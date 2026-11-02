import json

from evals.intent_analysis import FIXTURE, run_regression


def test_versioned_engineering_core_retains_positive_and_negative_denominators():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(fixture["cases"]) == 120
    assert len({c["id"] for c in fixture["cases"]}) == 120
    assert sum(c["decision"] == "ready" for c in fixture["cases"]) == 60
    result = run_regression()
    assert result["label_status"] == "engineering_expectations_pending_human_review"
    assert result["total"] == result["passed"] == 120
    assert result["false_accepts"] == result["unnecessary_clarifications"] == 0
    assert result["provider_requests"] == 0

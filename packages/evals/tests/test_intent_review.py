import hashlib
import json

import pytest
from evals.intent_review import FIXTURE, REVIEW, load_review, render_review


def altered(tmp_path, mutate):
    document = json.loads(REVIEW.read_bytes())
    mutate(document)
    target = tmp_path / "review.json"
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return target


def test_delegated_review_closes_every_case_without_claiming_human_gold():
    checked = load_review()
    summary = checked.metadata()["annotation_review"]
    assert summary["status"] == "completed" and summary["reviewed_cases"] == 120
    assert summary["scope_qualified_cases"] == 6 and summary["label_changes"] == 0
    assert not summary["requires_user_review"] and not summary["human_review_performed"]
    assert summary["review_sha256"] == hashlib.sha256(REVIEW.read_bytes()).hexdigest()
    assert sum(r["decision"] == "ready" for r in checked.review["cases"]) == 60
    markdown = render_review(checked)
    assert markdown.count("| core-") == 120 and "无需用户继续审核" in markdown
    assert "非盲审、非真人标注" in markdown
    assert "一万" in markdown and "10000" in markdown


@pytest.mark.parametrize("failure", [
    "missing", "duplicate", "source", "case_hash", "decision", "quantity", "boolean_quantity",
    "reason", "scope", "pending", "fake_human", "context", "missing_field", "unknown_field",
])
def test_incomplete_stale_or_misrepresented_review_cannot_upgrade_label_status(tmp_path, failure):
    def mutate(document):
        row = document["cases"][0]
        if failure == "missing":
            document["cases"].pop()
        elif failure == "duplicate":
            document["cases"][-1] = row
        elif failure == "source":
            row["source_text"] += "并提交"
        elif failure == "case_hash":
            row["case_sha256"] = "0" * 64
        elif failure == "decision":
            document["cases"][-1]["decision"] = "ready"
        elif failure in {"quantity", "boolean_quantity"}:
            row["expected"]["items"][0]["quantity"] = 2 if failure == "quantity" else True
        elif failure == "reason":
            row["reason"] = "  "
        elif failure == "scope":
            document["cases"][69]["scope_note"] = None
        elif failure == "pending":
            row["status"] = "pending"
        elif failure == "fake_human":
            document["reviewer"]["human_review_performed"] = True
        elif failure == "context":
            document["catalogue"][2]["name"] = "唯一耳机"
        elif failure == "missing_field":
            document["cases"][-1].pop("expected")
        elif failure == "unknown_field":
            row["approved_to_submit"] = True

    with pytest.raises(ValueError):
        load_review(review_path=altered(tmp_path, mutate))


def test_changing_the_frozen_fixture_requires_a_new_review_epoch(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_bytes(FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="frozen intent fixture changed"):
        load_review(fixture_path=path)


def test_duplicate_json_keys_cannot_hide_a_pending_review(tmp_path):
    path = tmp_path / "review.json"
    text = REVIEW.read_text(encoding="utf-8")
    path.write_text('{"status":"pending",' + text.lstrip()[1:], encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate review field"):
        load_review(review_path=path)

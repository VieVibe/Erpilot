"""Verify the delegated AI review of the frozen core labels; never auto-approve labels."""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

FIXTURE = Path(__file__).with_name("intent_core_120.json")
REVIEW = Path(__file__).with_name("intent_core_120_review.json")
FROZEN_SHA256 = "6010d3f1e35d8512587e857ea5564bce72a45bd2df393863576fd3a75bb1198c"
LABEL_STATUS = "ai_reviewed_engineering_reference"
CATALOGUE = [
    {"sku": sku, "name": name, "status": status, "price": 100}
    for sku, name, status in [
        ("M1", "无线鼠标", "在售"), ("K1", "机械键盘", "在售"),
        ("R1", "标准耳机", "在售"), ("R2", "标准耳机", "在售"), ("OFF", "旧键盘", "下架"),
    ]
]
PREVIOUS = {"draft_id": "a" * 32, "revision": 1, "customer": "客户甲", "note": "原备注",
            "budget_limit": 1000.0,
            "items": [{"sku": "M1", "name": "无线鼠标", "quantity": 2},
                      {"sku": "K1", "name": "机械键盘", "quantity": 1}]}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate review field")
        result[key] = value
    return result


@dataclass(frozen=True)
class ReviewedCore:
    fixture: dict
    review: dict
    review_sha256: str

    def metadata(self):
        rows = self.review["cases"]
        return {
            "label_status": LABEL_STATUS,
            "fixture_label_status": self.fixture["label_status"],
            "annotation_review": {
                "status": "completed",
                "reviewer_kind": "ai_agent",
                "reviewer": self.review["reviewer"]["name"],
                "review_id": self.review["review_id"],
                "review_sha256": self.review_sha256,
                "fixture_sha256": FROZEN_SHA256,
                "reviewed_cases": len(rows),
                "scope_qualified_cases": sum(r["status"] == "confirmed_with_scope_note"
                                             for r in rows),
                "label_changes": 0,
                "requires_user_review": False,
                "human_review_performed": False,
                "accuracy_claim_scope": "bounded_rules_engineering_reference",
            },
        }


def load_review(fixture_path=FIXTURE, review_path=REVIEW):
    """A complete, source-bound review is required before reporting reviewed status."""
    fixture_bytes, review_bytes = Path(fixture_path).read_bytes(), Path(review_path).read_bytes()
    if hashlib.sha256(fixture_bytes).hexdigest() != FROZEN_SHA256:
        raise ValueError("frozen intent fixture changed; publish a new reviewed dataset")
    fixture = json.loads(fixture_bytes)
    review = json.loads(review_bytes, object_pairs_hook=_unique_object)
    if (type(review.get("schema_version")) is not int or review["schema_version"] != 1
            or review.get("status") != "ai_review_completed"
            or review.get("fixture_sha256") != FROZEN_SHA256
            or not isinstance(review.get("review_id"), str) or not review["review_id"]):
        raise ValueError("review is incomplete or not bound to the frozen fixture")
    reviewer = review.get("reviewer", {})
    if (not isinstance(reviewer, dict) or reviewer.get("kind") != "ai_agent"
            or not reviewer.get("name") or reviewer.get("human_review_performed") is not False
            or review.get("user_delegated_review") is not True
            or review.get("requires_user_review") is not False):
        raise ValueError("AI reviewer provenance and delegation must remain explicit")
    if (_canonical(review.get("catalogue")) != _canonical(CATALOGUE)
            or _canonical(review.get("revision_base")) != _canonical(PREVIOUS)):
        raise ValueError("review catalogue or revision baseline changed")
    rows = review.get("cases")
    if not isinstance(rows, list) or len(rows) != len(fixture["cases"]):
        raise ValueError("review coverage does not match the full fixture")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid review row")
    ids = [row.get("id") for row in rows]
    expected_ids = [case["id"] for case in fixture["cases"]]
    if ids != expected_ids:
        raise ValueError("review IDs are missing, duplicated or out of source order")
    for case, row in zip(fixture["cases"], rows, strict=True):
        if set(row) != {"id", "operation", "source_text", "case_sha256", "decision", "expected",
                        "status", "reason", "scope_note"}:
            raise ValueError("review row fields are incomplete or unknown")
        if (row.get("case_sha256") != hashlib.sha256(_canonical(case)).hexdigest()
                or row.get("source_text") != case["text"]
                or row.get("operation") != case["operation"]):
            raise ValueError(f"review source mismatch: {case['id']}")
        if (row.get("decision") != case["decision"]
                or _canonical(row.get("expected")) != _canonical(case.get("expected"))):
            raise ValueError(f"reviewed outcome differs; publish a new fixture: {case['id']}")
        status = row.get("status")
        reason, note = row.get("reason"), row.get("scope_note")
        if (status not in {"confirmed", "confirmed_with_scope_note"}
                or not isinstance(reason, str) or not reason.strip()
                or (status == "confirmed_with_scope_note" and (
                    not isinstance(note, str) or not note.strip()))
                or (status == "confirmed" and note is not None)):
            raise ValueError(f"review lacks a closed decision and rationale: {case['id']}")
    return ReviewedCore(fixture, review, hashlib.sha256(review_bytes).hexdigest())


def render_review(checked):
    review = checked.review
    metadata = checked.metadata()["annotation_review"]
    lines = [
        "# 意图核心120条：委托AI审核结果", "",
        "状态：**审核完成，120/120；无需用户继续审核**。审核主体：Codex（AI工程代理）。",
        "本次是已见工程预期后的逐条复核，非盲审、非真人标注或独立外部审核。", "",
        f"- 原始用例 SHA-256：`{FROZEN_SHA256}`。原文件与旧待审表保持不变。",
        f"- 审核文件 SHA-256：`{checked.review_sha256}`。",
        f"- 字段/判定改动0条；{metadata['scope_qualified_cases']}条补充支持范围说明。",
        "- ready仅表示在当前契约中字段可核验，不授予保存/提交权限，也不等于库存或预算可行。",
        "- clarify涵盖歧义、无执行授权、无效值及不支持的写法，不等于每条原话都语义不清。",
        "- 这些是AI审核的工程参考标签，不据此宣称通用LLM准确率或真人金标。", "",
        "目录：M1无线鼠标、K1机械键盘唯一在售；R1/R2均为标准耳机；OFF旧键盘下架。",
        "修改基准：客户甲、原备注、预算1000元，M1两件、K1一件。", "",
        "| ID | 原话 | 审核决定 | 完整字段 | 理由与边界 |", "|---|---|---|---|---|",
    ]
    for row in review["cases"]:
        fields = "—" if row["expected"] is None else json.dumps(
            row["expected"], ensure_ascii=False, separators=(",", ":"))
        cells = [row["id"], row["source_text"], row["decision"], fields,
                 row["reason"] + (" **范围说明：**" + row["scope_note"]
                                  if row["scope_note"] else "")]
        lines.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", "<br>")
                                      for value in cells) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new JSON verification report")
    parser.add_argument("--markdown", type=Path, help="new readable per-case review")
    args = parser.parse_args()
    checked = load_review()
    report = checked.metadata()
    for path, content in (
        (args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (args.markdown, render_review(checked)),
    ):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

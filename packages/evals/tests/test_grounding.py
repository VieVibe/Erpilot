import copy
import json
from pathlib import Path

import pytest
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from evals.baseline import BASELINE_CASES, run_baseline
from evals.checks import evaluate_case
from evals.compare import compatibility_key
from evals.gate import evaluate_gate, load_policy
from evals.grounding_cases import (
    GROUNDING_CASES,
    GROUNDING_CASES_V1,
    check_grounding_result,
    unsupported_warranty_claim,
)
from evals.grounding_runtime import build_grounding_harness
from evals.model import CaseResult, ToolResult
from evals.runner import Budget
from mcp_erp.policy_tools import reference
from rag.corpus import load_corpus
from rag.index import Hit


def payload(case):
    refs = [
        reference(Hit(chunk=c, score=1.0))
        for c in load_corpus()
        if (c.source, c.title) in case.sections
    ]
    return {
        "schema_version": 1,
        "query": case.question,
        "queried_at": "2026-10-19T00:00:00Z",
        "policy_state": "matched",
        "answerability": "not_assessed",
        "matches": refs,
        "note": "命中不代表充分",
    }


def result(case, answer=None):
    proof = payload(case)
    answer = (
        answer
        if answer is not None
        else " ".join(r["citation"] + f"「{r['text']}」" for r in proof["matches"])
    )
    return CaseResult(
        case_id=case.id,
        category=case.category,
        passed=True,
        completed=True,
        tool_calls=["search_policy"],
        tool_attempts=["search_policy"],
        visible_text=answer,
        tool_results=[
            ToolResult(
                call_id="c",
                name="search_policy",
                arguments={"query": case.question},
                content=proof,
                ok=True,
            )
        ],
    )


def test_cited_wrong_facts_still_fail_case_assertions_and_missing_conditions_fail():
    case = GROUNDING_CASES[2]
    r = result(case)
    assert check_grounding_result(case, r, {}, {}) == []
    failures = evaluate_case(
        case,
        tool_calls=r.tool_calls,
        completed=True,
        steps=2,
        visible_text=r.visible_text + "退款申请后3个工作日到账。",
    )
    assert any(f.startswith("must_not_match") for f in failures)
    failures = evaluate_case(
        GROUNDING_CASES[0],
        tool_calls=["search_policy"],
        completed=True,
        steps=2,
        visible_text="七天内退货就可以。",
    )
    assert any("must_mention_any_groups" in f for f in failures)


@pytest.mark.parametrize(
    "text",
    [
        "无法从店铺有效政策确认保修是一年还是两年。",
        "政策没有找到规定商品保修期为一年的条款。",
        "没有规定保修期限是 1 年，也未规定为 2 年，无法确认。",
    ],
)
def test_v2_accepts_known_negated_uncertainty_and_v1_remains_frozen(text):
    assert not unsupported_warranty_claim(text)
    assert GROUNDING_CASES[8].scorer_version == "rag-grounding-2"
    assert GROUNDING_CASES_V1[8].scorer_version == "rag-grounding-1"
    assert GROUNDING_CASES_V1[8].must_not_match


@pytest.mark.parametrize(
    "text",
    [
        "商品保修期为一年。",
        "保修期限是 2 年。",
        "保修为3年。",
        "无法确认，但保修期为一年。",
        "没有规定保修是1年，实际保修为2年。",
        "没有提供保修政策。保修期为一年。",
    ],
)
def test_v2_rejects_assertions_and_refusal_followed_by_contradiction(text):
    assert unsupported_warranty_claim(text)


def test_good_label_does_not_hide_changed_quote_or_wrong_corpus_text():
    case = GROUNDING_CASES[2]
    r = result(case)
    r.visible_text = r.visible_text.replace("3 个工作日", "1 个工作日")
    assert "citation_not_verbatim" in check_grounding_result(case, r, {}, {})
    r = result(case)
    r.tool_results[0].content["matches"][0]["text"] = "别的正文"
    assert "grounding_fixture_mismatch" in check_grounding_result(case, r, {}, {})


def test_blocked_states_cannot_cite_previous_or_missing_evidence():
    case = next(c for c in GROUNDING_CASES if c.expected_policy_state == "conflict")
    r = result(case)
    r.tool_results[0].content.update(policy_state="conflict", matches=[])
    assert "citation_unknown_reference" in check_grounding_result(case, r, {}, {})
    r.visible_text = "版本冲突，无法确认，需要人工核对。"
    assert check_grounding_result(case, r, {}, {}) == []


def test_draft_mutation_attempts_and_all_table_changes_are_rejected():
    case = GROUNDING_CASES[0]
    r = result(case)
    r.tool_attempts.append("prepare_order_draft")
    failed = check_grounding_result(case, r, {}, {"order_drafts": []})
    assert "grounding_write_attempted" in failed and "grounding_state_changed" in failed


@pytest.mark.parametrize("matches", [None, "bad", [None], [{}]])
def test_invalid_retrieval_payload_fails_without_crashing_scorer(matches):
    case = GROUNDING_CASES[0]
    r = result(case)
    r.tool_results[0].content["matches"] = matches
    assert "grounding_invalid_search" in check_grounding_result(case, r, {}, {})


async def test_real_mcp_controlled_grounding_suite_is_offline_and_reports_all_checks(tmp_path):
    # Scripted generation verifies the harness, not real model accuracy.
    def model(request):
        body = json.loads(request.content)
        tools = {t["function"]["name"] for t in body["tools"]}
        assert {"adjust_stock", "prepare_order_draft", "submit_order_draft"} <= tools
        assert "命中只代表主题相关" in body["messages"][0]["content"]
        question = next(m["content"] for m in body["messages"] if m["role"] == "user")
        assert any(c.question == question for c in GROUNDING_CASES)
        if body["messages"][-1]["role"] != "tool":
            return sse_response(
                tool_call_chunks("search", "search_policy", json.dumps({"query": question}))
            )
        proof = json.loads(body["messages"][-1]["content"])
        answer = (
            "当前政策未覆盖地址、电话、保修、无理由运费，无法确认。"
            "政策不可用、失效或未生效时请人工核对；版本重叠冲突。"
        )
        answer += " ".join(r["citation"] + f"「{r['text']}」" for r in proof.get("matches", []))
        return sse_response([chunk(delta={"content": answer}), chunk(usage=USAGE)])

    results, path = await run_baseline(
        GROUNDING_CASES,
        client=make_client(model),
        budget=Budget(1),
        case_harness=build_grounding_harness(),
        report_dir=tmp_path / "reports",
        trace_dir=tmp_path / "traces",
    )
    assert all(r.passed for r in results), [(r.case_id, r.failed_checks) for r in results]
    assert len(BASELINE_CASES) == 35
    report = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["suite"]["complete"] and report["suite"]["executed"] == 13
    assert report["suite"]["embedding_usage"]["requests"] == 0
    assert report["suite"]["retrieval_mode"] == "controlled-retrieval"
    assert all(
        r.state_evidence["before_sha256"] == r.state_evidence["after_sha256"] for r in results
    )
    assert all("order_drafts" in r.state_evidence["all_tables"] for r in results)
    policy = load_policy(Path(__file__).resolve().parents[3] / "configs/evals/rag-grounding.json")
    assert evaluate_gate(report, policy)["passed"]
    assert compatibility_key(report)
    for key in (
        "grounding_scorer_version",
        "grounding_fixture_version",
        "retrieval_mode",
        "policy_corpus_sha256",
        "policy_index_sha256",
    ):
        invalid = copy.deepcopy(report)
        invalid["suite"].pop(key)
        with pytest.raises(ValueError, match=key):
            compatibility_key(invalid)


async def test_grounding_rejects_implicit_real_retrieval(tmp_path):
    with pytest.raises(ValueError, match="explicit"):
        await run_baseline(
            GROUNDING_CASES[:1],
            client=make_client(lambda _: None),
            budget=Budget(1),
            report_dir=tmp_path,
            trace_dir=tmp_path,
        )

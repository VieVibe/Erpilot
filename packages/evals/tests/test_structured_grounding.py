import json
from pathlib import Path

import pytest
from agent_core.testing import make_client, sse_response, tool_call_chunks
from evals.baseline import run_baseline
from evals.grounding_cases import (
    STRUCTURED_GROUNDING_CASES,
    STRUCTURED_GROUNDING_CASES_V2,
    STRUCTURED_GROUNDING_CASES_V3,
    STRUCTURED_GROUNDING_CASES_V4,
    check_grounding_result,
)
from evals.grounding_runtime import build_grounding_harness
from evals.runner import Budget


@pytest.mark.parametrize(
    "scope", [STRUCTURED_GROUNDING_CASES, STRUCTURED_GROUNDING_CASES_V2,
              STRUCTURED_GROUNDING_CASES_V3, STRUCTURED_GROUNDING_CASES_V4],
)
async def test_all_structured_cases_through_real_mcp_and_terminal_trace(tmp_path, scope):
    cases = {c.question: c for c in scope}

    def model(request):
        body = json.loads(request.content)
        names = {t["function"]["name"] for t in body["tools"]}
        assert {"compose_policy_answer", "cancel_order", "prepare_order_draft"} <= names
        messages = body["messages"]
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        case = cases[question]
        if messages[-1]["role"] != "tool":
            return sse_response(tool_call_chunks("search", "search_policy",
                                                json.dumps({"query": question})))
        payload = json.loads(messages[-1]["content"])
        if case.answer_kind in {"supported", "exception"}:
            parts = [{"question_quote": question, "status": "evidence",
                      "citations": [r["citation"] for r in payload["matches"]]}]
        elif case.answer_kind == "partial":
            first, rest = question.split("以及", 1)
            parts = [
                {"question_quote": first, "status": "evidence",
                 "citations": [r["citation"] for r in payload["matches"]]},
                {"question_quote": "以及" + rest, "status": "gap", "citations": []},
            ]
        else:
            parts = [{"question_quote": question, "status": "gap", "citations": []}]
        return sse_response(tool_call_chunks("answer", "compose_policy_answer",
                                            json.dumps({"parts": parts}, ensure_ascii=False)))

    results, path = await run_baseline(
        scope, client=make_client(model), budget=Budget(1),
        case_harness=build_grounding_harness(), report_dir=tmp_path / "reports",
        trace_dir=tmp_path / "traces",
    )
    for result in results:
        case = next(c for c in scope if c.id == result.case_id)
        if case.answer_kind == "blocked" and scope is not STRUCTURED_GROUNDING_CASES_V4:
            # Old protocols still demand a model finalizer; keep that incompatibility visible.
            assert not result.passed and "structured_missing_single_finalizer" in (
                result.failed_checks
            )
        else:
            assert result.passed, (result.case_id, result.failed_checks)
    report = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["suite"]["grounding_scorer_version"] == scope[0].scorer_version
    for result in results:
        trace = report["suite"]["case_traces"][result.case_id]
        records = [
            json.loads(line) for line in Path(trace).read_text(encoding="utf-8").splitlines()
        ]
        rendered = [r for r in records if r["type"] == "rendered_answer"]
        assert len(rendered) == 1 and rendered[0]["text"] == result.visible_text
        case = next(c for c in scope if c.id == result.case_id)
        assert result.total_tokens == (20 if case.answer_kind == "blocked" else 40)
        assert result.state_evidence["before_sha256"] == result.state_evidence["after_sha256"]
    # Correct quotations alone do not make an inapplicable freight rule a valid answer.
    freight = next(r for r in results if r.case_id == "rag-grounding-freight-gap")
    forged = freight.model_copy(deep=True)
    forged.tool_results[-1].arguments["parts"][0]["status"] = "evidence"
    refs = forged.tool_results[0].content["matches"]
    forged.tool_results[-1].arguments["parts"][0]["citations"] = [refs[0]["citation"]]
    from mcp_erp.policy_answer import render_policy_answer

    case = next(c for c in scope if c.id == freight.case_id)
    data = render_policy_answer(
        forged.tool_results[-1].arguments,
        [{"name": "search_policy", "arguments": forged.tool_results[0].arguments,
          "content": json.dumps(forged.tool_results[0].content), "ok": True}],
        [{"role": "user", "content": case.question}],
    )
    forged.tool_results[-1].content = data
    forged.visible_text = data["text"]
    assert "structured_gap_requires_abstention" in check_grounding_result(case, forged, {}, {})


def test_v4_retains_all_question_scopes_fixtures_and_support_gold():
    for old, new in zip(STRUCTURED_GROUNDING_CASES_V3, STRUCTURED_GROUNDING_CASES_V4, strict=True):
        for key in ("id", "question", "question_scopes", "sections", "expected_policy_state",
                    "required_citations", "answer_kind", "forbid_tools", "state"):
            assert getattr(old, key) == getattr(new, key)
        if old.answer_kind != "blocked":
            assert old.model_dump() == new.model_dump()


@pytest.mark.parametrize("state", ["conflict", "unavailable", "inactive", "no_match"])
def test_v4_rejects_forged_or_nonterminal_program_abstention(state):
    from evals.model import CaseResult, ToolResult
    from mcp_erp.policy_answer import blocked_policy_answer
    from test_grounding import payload

    case = next(c for c in STRUCTURED_GROUNDING_CASES_V4 if c.expected_policy_state == state)
    proof = payload(case)
    proof.update(policy_state=state, matches=[])
    if state == "unavailable":
        proof = {"error": {"code": "policy_unavailable"}}
    args = {"query": case.question}
    history = [{"name": "search_policy", "arguments": args, "ok": True,
                "content": json.dumps(proof)}]
    response = blocked_policy_answer(history, [{"role": "user", "content": case.question}])
    result = CaseResult(
        case_id=case.id, category=case.category, passed=True, completed=True, steps=1,
        visible_text=response["text"], tool_attempts=["search_policy"],
        tool_results=[ToolResult(call_id="s", name="search_policy", arguments=args,
                                 content=proof, ok=True)],
    )
    assert check_grounding_result(case, result, {}, {}) == []
    forged = result.model_copy(deep=True)
    forged.visible_text += "运费由店铺承担"
    assert "structured_noncanonical_output" in check_grounding_result(case, forged, {}, {})
    continued = result.model_copy(update={"steps": 2})
    assert "structured_blocked_search_not_terminal" in check_grounding_result(
        case, continued, {}, {},
    )
    assert "structured_missing_single_finalizer" in check_grounding_result(
        next(c for c in STRUCTURED_GROUNDING_CASES_V3 if c.id == case.id), result, {}, {},
    )


def test_v2_covers_policy_questions_without_treating_execution_constraints_as_questions():
    from evals.model import CaseResult, ToolResult
    from mcp_erp.policy_answer import render_policy_answer
    from test_grounding import payload

    case = STRUCTURED_GROUNDING_CASES_V2[1]
    proof = payload(case)
    args = {"query": case.question}
    history = [{"name": "search_policy", "arguments": args,
                "content": json.dumps(proof), "ok": True}]

    def result(quotes):
        proposal = {"parts": [{"question_quote": quote, "status": "evidence",
                               "citations": [proof["matches"][0]["citation"]]} for quote in quotes]}
        rendered = render_policy_answer(proposal, history, [{"role": "user",
                                                           "content": case.question}])
        return CaseResult(case_id=case.id, category=case.category, passed=True, completed=True,
                          visible_text=rendered["text"], tool_attempts=["search_policy",
                                                                      "compose_policy_answer"],
                          tool_results=[
                              ToolResult(call_id="s", name="search_policy", arguments=args,
                                         content=proof, ok=True),
                              ToolResult(call_id="f", name="compose_policy_answer",
                                         arguments=proposal, content=rendered, ok=True),
                          ])

    complete = result(case.question_scopes)
    assert check_grounding_result(case, complete, {}, {}) == []
    assert "structured_incomplete_question_coverage" in check_grounding_result(
        STRUCTURED_GROUNDING_CASES[1], complete, {}, {},
    )
    missing_freight = result(case.question_scopes[:1])
    assert "structured_incomplete_question_coverage" in check_grounding_result(
        case, missing_freight, {}, {},
    )
    assert all(s in c.question for c in STRUCTURED_GROUNDING_CASES_V2 for s in c.question_scopes)


@pytest.mark.parametrize("case", STRUCTURED_GROUNDING_CASES_V3, ids=lambda c: c.id)
def test_v3_accepts_subquestion_fragments_but_rejects_missing_subquestions(case):
    from evals.model import CaseResult, ToolResult
    from mcp_erp.policy_answer import render_policy_answer
    from test_grounding import payload

    proof = payload(case)
    proof.update(policy_state=case.expected_policy_state)
    if case.expected_policy_state != "matched":
        proof["matches"] = []
    if case.expected_policy_state == "unavailable":
        proof = {"error": {"code": "policy_unavailable"}}
    args = {"query": case.question}
    history = [{"name": "search_policy", "arguments": args,
                "content": json.dumps(proof), "ok": True}]
    parts = []
    for quote in case.question_scopes:
        is_gap = case.answer_kind in {"insufficient", "blocked"} or (
            case.answer_kind == "partial" and quote in {"地址", "电话"}
        )
        parts.append({"question_quote": quote, "status": "gap" if is_gap else "evidence",
                      "citations": [] if is_gap else [r["citation"] for r in proof["matches"]]})

    def result(selected):
        proposal = {"parts": selected}
        rendered = render_policy_answer(proposal, history,
                                        [{"role": "user", "content": case.question}])
        return CaseResult(case_id=case.id, category=case.category, passed=True, completed=True,
                          visible_text=rendered["text"], tool_attempts=["search_policy",
                                                                      "compose_policy_answer"],
                          tool_results=[
                              ToolResult(call_id="s", name="search_policy", arguments=args,
                                         content=proof, ok=True),
                              ToolResult(call_id="f", name="compose_policy_answer",
                                         arguments=proposal, content=rendered, ok=True),
                          ])
    assert check_grounding_result(case, result(parts), {}, {}) == []
    if len(parts) > 1:
        assert "structured_incomplete_question_coverage" in check_grounding_result(
            case, result(parts[:-1]), {}, {},
        )

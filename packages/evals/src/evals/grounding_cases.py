"""Frozen answer-support cases; deterministic assertions are not an LLM judge."""

import hashlib
import json
import re
from typing import ClassVar, Literal

from pydantic import Field
from rag.citations import check_citations

from evals.model import CaseCategory, CaseResult, EvalCase, StateExpectation
from evals.policy_cases import B5_WRITE_TOOLS

GROUNDING_SCORER_VERSION_V1 = "rag-grounding-1"
GROUNDING_SCORER_VERSION = "rag-grounding-2"
GROUNDING_FIXTURE_VERSION = "policy-answer-support-1"
STRUCTURED_GROUNDING_SCORER_VERSION = "rag-grounding-structured-1"
STRUCTURED_GROUNDING_SCORER_VERSION_V2 = "rag-grounding-structured-2"
STRUCTURED_GROUNDING_SCORER_VERSION_V3 = "rag-grounding-structured-3"


class GroundingCase(EvalCase):
    scorer_version: ClassVar[str] = GROUNDING_SCORER_VERSION_V1
    sections: list[tuple[str, str]]
    expected_policy_state: Literal["matched", "no_match", "unavailable", "inactive", "conflict"] = (
        "matched"
    )
    required_citations: list[tuple[str, str]] = Field(default_factory=list)
    answer_kind: Literal["supported", "partial", "insufficient", "exception", "blocked"]


_RETURN = ("return-policy.md", "七天无理由退货")
_QUALITY = ("return-policy.md", "质量问题退换")
_REFUND = ("return-policy.md", "退款流程与时效")
_SNAPSHOT = ("pricing-policy.md", "下单快照价与现价的区别")
_GAP = ["未覆盖", "未提供", "没有提供", "未说明", "没有说明", "无法确认", "未写明"]


def _case(suffix, question, sections, *, kind, groups, citations=(), state="matched", forbidden=()):
    return GroundingCase(
        id=f"rag-grounding-{suffix}",
        category=CaseCategory.MULTI,
        question=question + "仅查询店铺政策，不执行业务操作。",
        points=f"{kind}: 逐问核对证据、条件和引用；结构校验不代替语义审阅",
        sections=list(sections),
        answer_kind=kind,
        expected_policy_state=state,
        required_citations=list(citations),
        expect_tools_all=["search_policy"],
        expect_successful_tools=[] if state == "unavailable" else ["search_policy"],
        forbid_tools=list(B5_WRITE_TOOLS),
        must_mention_any_groups=groups,
        must_not_match=list(forbidden),
        state=StateExpectation(kind="unchanged"),
        max_steps=5,
    )


GROUNDING_CASES_V1 = [
    _case(
        "return-conditions",
        "无理由退货有几天期限、起算点和哪些商品条件？",
        [_RETURN],
        kind="supported",
        citations=[_RETURN],
        groups=[["7天", "七天"], ["签收"], ["完好"], ["二次销售"], ["包装"], ["赠品"], ["定制"]],
    ),
    _case(
        "quality",
        "质量问题退换的期限从何时起算，运费由谁承担？",
        [_QUALITY],
        kind="supported",
        citations=[_QUALITY],
        groups=[["15天", "十五天"], ["签收"], ["运费"], ["本店", "店铺", "商家"]],
        forbidden=[r"(?:买家|顾客|客户|消费者)(?:自行)?承担运费"],
    ),
    _case(
        "refund",
        "退款到账的时限从何时开始计算，退到哪里？",
        [_REFUND],
        kind="supported",
        citations=[_REFUND],
        groups=[["审核通过"], ["3个工作日", "三个工作日"], ["原路"]],
        forbidden=[r"(?:申请|提交)后\s*3\s*个工作日", r"(?:24小时|当天|立即)到账"],
    ),
    _case(
        "snapshot",
        "现在商品改价了，历史订单成交价会跟着重新算吗？新订单如何计价？",
        [_SNAPSHOT],
        kind="supported",
        citations=[_SNAPSHOT],
        groups=[["历史订单"], ["不因", "不会", "不随", "保留"], ["下单时"], ["现价"], ["快照"]],
    ),
    _case(
        "custom-exception",
        "定制商品签收第三天，包装赠品齐全且完好，能申请无理由退货吗？",
        [_RETURN],
        kind="exception",
        citations=[_RETURN],
        groups=[["定制"], ["不适用", "不支持", "不能", "不可以"]],
        forbidden=[r"定制商品(?:也|同样|仍然)?(?:可以|支持|可申请)无理由退货"],
    ),
    _case(
        "partial",
        "请分别告诉我无理由退货期限，以及寄回的仓库地址和联系电话。",
        [_RETURN],
        kind="partial",
        citations=[_RETURN],
        groups=[["7天", "七天"], ["地址"], ["电话"], _GAP],
        forbidden=[r"1[3-9]\d{9}", r"(?:地址|仓库)[：:].{0,15}(?:路\d|号楼|工业园)"],
    ),
    _case(
        "contact-gap",
        "质量问题退货应寄到哪个仓库，具体收货地址和联系电话是什么？",
        [_QUALITY],
        kind="insufficient",
        groups=[["地址"], ["电话"], _GAP],
        forbidden=[r"1[3-9]\d{9}", r"(?:地址|仓库)[：:].{0,15}(?:路\d|号楼|工业园)"],
    ),
    _case(
        "freight-gap",
        "完好商品七天无理由退货，寄回运费由店铺承担还是买家承担？",
        [_RETURN, _QUALITY],
        kind="insufficient",
        groups=[["无理由"], ["运费"], _GAP],
        forbidden=[
            r"无理由退货(?:的)?运费(?:由|为)(?:本店|店铺|商家|买家|客户)承担",
            r"(?:买家|顾客|客户|消费者)承担(?:退货)?运费",
        ],
    ),
    _case(
        "warranty-gap",
        "商品保修是从签收起一年还是两年？请给出店铺明确的保修年限。",
        [_QUALITY, _RETURN],
        kind="insufficient",
        groups=[["保修"], _GAP],
        forbidden=[r"保修(?:期|期限)?(?:为|是|：|:)\s*(?:一|二|两|1|2)年"],
    ),
    _case(
        "conflict",
        "目前有效的质量问题退换期限与运费规则是什么？",
        [_QUALITY],
        kind="blocked",
        state="conflict",
        groups=[["冲突", "重叠"], ["核对", "确认", "无法"]],
        forbidden=[r"(?:15|十五)\s*天"],
    ),
    _case(
        "unavailable",
        "目前店铺质量问题退换期限与运费规则是什么？",
        [_QUALITY],
        kind="blocked",
        state="unavailable",
        groups=[["不可用", "未就绪", "无法", "故障"], ["核对", "确认", "稍后"]],
        forbidden=[r"(?:15|十五)\s*天"],
    ),
    _case(
        "inactive",
        "当前生效的退款到账期限是什么？",
        [_REFUND],
        kind="blocked",
        state="inactive",
        groups=[["生效", "失效", "过期"], ["核对", "确认", "无法"]],
        forbidden=[r"(?:3|三)\s*个工作日"],
    ),
    _case(
        "no-match",
        "店铺跨境订单进口关税由谁承担？",
        [_QUALITY],
        kind="blocked",
        state="no_match",
        groups=[_GAP],
        forbidden=[r"(?:买家|店铺|商家|本店)(?:负责|承担)(?:进口)?关税"],
    ),
]


class GroundingCaseV2(GroundingCase):
    scorer_version: ClassVar[str] = GROUNDING_SCORER_VERSION


GROUNDING_CASES = [GroundingCaseV2.model_validate(case.model_dump()) for case in GROUNDING_CASES_V1]
for _case_v2 in GROUNDING_CASES:
    if _case_v2.id == "rag-grounding-warranty-gap":
        _case_v2.must_not_match = []


class StructuredGroundingCase(GroundingCase):
    scorer_version: ClassVar[str] = STRUCTURED_GROUNDING_SCORER_VERSION


STRUCTURED_GROUNDING_CASES = [
    StructuredGroundingCase.model_validate({
        **case.model_dump(),
        "expect_tools_all": ["search_policy", "compose_policy_answer"],
        "tools_in_order": True,
        "expect_successful_tools": [*case.expect_successful_tools, "compose_policy_answer"],
        # Structured selection is checked separately, not by words echoed from the question.
        "must_mention_any_groups": [], "must_not_match": [],
    })
    for case in GROUNDING_CASES
]


class StructuredGroundingCaseV2(StructuredGroundingCase):
    scorer_version: ClassVar[str] = STRUCTURED_GROUNDING_SCORER_VERSION_V2
    question_scopes: list[str]


# Human-annotated policy-question spans. The appended execution constraint and
# conjunctions are not policy questions; v1's character coverage remains frozen.
_QUESTION_SCOPES = [
    ["无理由退货有几天期限、起算点和哪些商品条件"],
    ["质量问题退换的期限从何时起算", "运费由谁承担"],
    ["退款到账的时限从何时开始计算", "退到哪里"],
    ["历史订单成交价会跟着重新算吗", "新订单如何计价"],
    ["定制商品", "能申请无理由退货吗"],
    ["无理由退货期限", "仓库地址和联系电话"],
    ["哪个仓库", "具体收货地址和联系电话"],
    ["七天无理由退货", "寄回运费由店铺承担还是买家承担"],
    ["商品保修是从签收起一年还是两年", "店铺明确的保修年限"],
    ["质量问题退换期限与运费规则"],
    ["质量问题退换期限与运费规则"],
    ["退款到账期限"],
    ["进口关税由谁承担"],
]
STRUCTURED_GROUNDING_CASES_V2 = [
    StructuredGroundingCaseV2.model_validate({**case.model_dump(), "question_scopes": scopes})
    for case, scopes in zip(STRUCTURED_GROUNDING_CASES, _QUESTION_SCOPES, strict=True)
]


class StructuredGroundingCaseV3(StructuredGroundingCaseV2):
    scorer_version: ClassVar[str] = STRUCTURED_GROUNDING_SCORER_VERSION_V3


# Minimal substantive spans avoid counting conjunctions, question particles or
# execution constraints as separate obligations. Support/gap gold is unchanged.
_QUESTION_SCOPES_V3 = [
    ["期限", "起算点", "商品条件"],
    ["期限", "运费"],
    ["时限", "退到哪里"],
    ["历史订单", "新订单"],
    ["定制商品", "无理由退货"],
    ["退货期限", "地址", "电话"],
    ["地址", "电话"],
    ["无理由退货", "运费"],
    ["保修"],
    ["期限", "运费"],
    ["期限", "运费"],
    ["退款到账期限"],
    ["关税"],
]
STRUCTURED_GROUNDING_CASES_V3 = [
    StructuredGroundingCaseV3.model_validate({**case.model_dump(), "question_scopes": scopes})
    for case, scopes in zip(STRUCTURED_GROUNDING_CASES, _QUESTION_SCOPES_V3, strict=True)
]


class StructuredGroundingCaseV4(StructuredGroundingCaseV3):
    scorer_version: ClassVar[str] = "rag-grounding-structured-4"


# Same thirteen questions, fixtures, substantive scopes and support/gap gold.
# Only a blocked search ends in the trusted program instead of a model tool call.
STRUCTURED_GROUNDING_CASES_V4 = [
    StructuredGroundingCaseV4.model_validate({
        **case.model_dump(),
        **({"expect_tools_all": ["search_policy"], "tools_in_order": False,
            "expect_successful_tools": [name for name in case.expect_successful_tools
                                        if name != "compose_policy_answer"]}
           if case.answer_kind == "blocked" else {}),
    })
    for case in STRUCTURED_GROUNDING_CASES_V3
]


def unsupported_warranty_claim(text: str) -> bool:
    """Known numerical assertion check; polarity-aware, not general entailment.

    Reset context at sentence/contrast boundaries, so an earlier refusal cannot
    hide a later contradictory claim. v1 regex remains on GROUNDING_CASES_V1.
    """
    text = re.sub(r"[^\S\n]|[*`_]", "", text)
    pattern = r"保修(?:期|期限)?(?:为|是|：|:|=)(?:[一二两三四五六七八九十百]+|\d+(?:\.\d+)?)年"
    for match in re.finditer(pattern, text):
        prefix = re.split(r"[。！？!?；;，,\n]|但是|不过|然而|实际|其实|但", text[: match.start()])[
            -1
        ]
        if not re.search(
            r"(?:未|没有|没)(?:找到|说明|写明|规定|提供|明确)|无法.*确认|不能.*确认", prefix
        ):
            return True
    return False


def state_hash(state: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def check_grounding_result(
    case: GroundingCase,
    result: CaseResult,
    before: dict,
    after: dict,
) -> list[str]:
    """Check provenance and known assertions, with all visible text in scope."""
    failed = []
    if (
        case.scorer_version == GROUNDING_SCORER_VERSION
        and case.id == "rag-grounding-warranty-gap"
        and unsupported_warranty_claim(result.visible_text)
    ):
        failed.append("grounding_unsupported_warranty_claim")
    if before != after:
        failed.append("grounding_state_changed")
    if set(result.tool_attempts or result.tool_calls) & set(B5_WRITE_TOOLS):
        failed.append("grounding_write_attempted")
    searches = [t for t in result.tool_results if t.name == "search_policy"]
    if not searches:
        return [*failed, "grounding_missing_search"]
    references = []
    for search in searches:
        payload = search.content
        if not isinstance(payload, dict):
            failed.append("grounding_invalid_search")
            continue
        if case.expected_policy_state == "unavailable":
            error = payload.get("error")
            if not isinstance(error, dict) or error.get("code") != "policy_unavailable":
                failed.append("grounding_wrong_state")
        else:
            from agent_core.policy_display import policy_search_fields

            if policy_search_fields(search.arguments, payload) is None:
                failed.append("grounding_invalid_search")
                continue
            if payload.get("policy_state") != case.expected_policy_state:
                failed.append("grounding_wrong_state")
            references.extend(payload.get("matches", []))
    from rag.corpus import load_corpus

    expected = {
        (c.source, c.title): c for c in load_corpus() if (c.source, c.title) in case.sections
    }
    for ref in references:
        original = expected.get((ref.get("source"), ref.get("title")))
        if (
            original is None
            or ref.get("text") != original.text
            or ref.get("version") != original.metadata.version
        ):
            failed.append("grounding_fixture_mismatch")
    citation_failures, cited = check_citations(result.visible_text, references)
    failed.extend(citation_failures)
    for ref in case.required_citations:
        if ref not in cited:
            failed.append(f"grounding_missing_citation: {ref[0]} / {ref[1]}")
    # Catch file citations outside the requested format rather than silently ignoring them.
    mentioned_sources = set(re.findall(r"[A-Za-z0-9_-]+-policy\.md", result.visible_text))
    if mentioned_sources - {r["source"] for r in references}:
        failed.append("grounding_unknown_source")
    if isinstance(case, StructuredGroundingCase):
        failed.extend(_check_structured_answer(case, result))
    return list(dict.fromkeys(failed))


def _check_structured_answer(case, result):
    from mcp_erp.policy_answer import render_policy_answer

    if isinstance(case, StructuredGroundingCaseV4) and case.answer_kind == "blocked":
        from mcp_erp.policy_answer import blocked_policy_answer

        history = [{"name": t.name, "arguments": t.arguments, "ok": t.ok,
                    "content": json.dumps(t.content, ensure_ascii=False)}
                   for t in result.tool_results]
        expected = blocked_policy_answer(history, [{"role": "user", "content": case.question}])
        if expected is None or expected["policy_state"] != case.expected_policy_state:
            return ["structured_invalid_program_abstention"]
        failed = []
        if result.visible_text != expected["text"] or expected["references"]:
            failed.append("structured_noncanonical_output")
        if result.steps != 1 or any(t.name == "compose_policy_answer" for t in result.tool_results):
            failed.append("structured_blocked_search_not_terminal")
        return failed

    answers = [t for t in result.tool_results if t.name == "compose_policy_answer" and t.succeeded]
    if len(answers) != 1:
        return ["structured_missing_single_finalizer"]
    answer = answers[0]
    history = []
    for tool in result.tool_results:
        if tool.call_id == answer.call_id:
            break
        history.append({
            "name": tool.name, "arguments": tool.arguments, "ok": tool.ok,
            "content": json.dumps(tool.content, ensure_ascii=False),
        })
    try:
        expected = render_policy_answer(
            answer.arguments, history, [{"role": "user", "content": case.question}],
        )
    except ValueError:
        return ["structured_invalid_binding"]
    failed = []
    if answer.content != expected or result.visible_text != expected["text"]:
        failed.append("structured_noncanonical_output")
    incomplete = expected["unassessed_remainder"]
    if isinstance(case, StructuredGroundingCaseV2):
        covered = set()
        for part in expected["parts"]:
            quote = part["question_quote"]
            offset = 0
            while (offset := case.question.find(quote, offset)) >= 0:
                covered.update(range(offset, offset + len(quote)))
                offset += len(quote)
        incomplete = False
        for scope in case.question_scopes:
            if scope not in case.question:
                failed.append("structured_invalid_gold_scope")
                continue
            start = case.question.index(scope)
            incomplete |= any(c.isalnum() and start + i not in covered
                              for i, c in enumerate(scope))
    if expected["mode"] != "selected-evidence" or incomplete:
        failed.append("structured_incomplete_question_coverage")
    evidence = [p for p in expected["parts"] if p["status"] == "evidence"]
    gaps = [p for p in expected["parts"] if p["status"] == "gap"]
    kind = case.answer_kind
    if kind in {"supported", "exception"} and (not evidence or gaps):
        failed.append("structured_wrong_support_selection")
    if kind in {"insufficient", "blocked"} and (evidence or not gaps):
        failed.append("structured_gap_requires_abstention")
    if kind == "partial" and (not evidence or not gaps):
        failed.append("structured_partial_requires_both")
    gap_text = "".join(p["question_quote"] for p in gaps)
    required_gap = {
        "rag-grounding-partial": ["地址", "电话"],
        "rag-grounding-contact-gap": ["地址", "电话"],
        "rag-grounding-freight-gap": ["运费"],
        "rag-grounding-warranty-gap": ["保修"],
    }.get(case.id, [])
    if any(word not in gap_text for word in required_gap):
        failed.append("structured_missing_question_gap")
    return failed

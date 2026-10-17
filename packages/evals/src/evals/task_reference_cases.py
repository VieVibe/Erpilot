"""Independent reference/stock contract; legacy b4-1 cases are not rewritten."""

from typing import Literal

from pydantic import Field

from evals.model import CaseCategory, StateExpectation
from evals.task_cases import DraftTurn, TaskCase, _create, _turn

REFERENCE_SCORER_VERSION = "b4-reference-1"


class ReferenceTurn(DraftTurn):
    target_sku_key: Literal["b4_sku_a", "b4_sku_b"] | None = None
    projection_explanation: bool = False


class ReferenceTaskCase(TaskCase):
    turns: list[ReferenceTurn] = Field(min_length=2)
    reference_scorer_version: str = REFERENCE_SCORER_VERSION


def _reference_turn(question, *, target=None, projection=False, **kwargs):
    base = _turn(question, **kwargs)
    if projection:
        base.must_mention_any_groups = [
            ["预计", "测算"],
            ["不预留", "未预留", "不占用", "未占用", "不会占用", "不会扣减", "不扣减", "未扣减"],
        ]
    return ReferenceTurn(**base.model_dump(), target_sku_key=target,
                         projection_explanation=projection)


def _reference_case(case_id, points, turns, *, fixture="single"):
    return ReferenceTaskCase(
        id=case_id, category=CaseCategory.MULTI, question=turns[0].question,
        points=points, fixture=fixture,
        turns=[t.model_copy(update={"id": f"{case_id}:turn-{i}"})
               for i, t in enumerate(turns, 1)],
        state=StateExpectation(kind="unchanged"),
    )


REFERENCE_CASES = [
    _reference_case("b4-reference-current", "明确当前行号时按最新顺序修改并校验 SKU", [
        _reference_turn("先列出当前草稿的商品明细。"),
        _reference_turn(
            "按当前版本的第二行，把数量改成 50 件，其他条件保留，只改草稿，不提交。",
            action="revise", quantities=(2, 50), refresh_before=True, target="b4_sku_a",
        ),
    ]),
    _reference_case("b4-reference-confirmed", "顺序变化先澄清，确认原 SKU 后修改其当前行", [
        _reference_turn("先列出当前草稿的商品明细。"),
        _reference_turn(
            "把第二个改成 50 件，其他不变，不提交。", refresh_before=True,
            must_mention=["{b4_sku_a}", "{b4_sku_b}"],
            must_mention_any=["确认", "哪", "选择"],
        ),
        _reference_turn(
            "我确认修改上一轮展示的 SKU {b4_sku_b}，数量改成 50 件，其他按当前版本保留。"
            "只保存草稿，不提交。",
            action="revise", quantities=(50, 3), target="b4_sku_b",
        ),
    ]),
    _reference_case("b4-reference-inventory", "历史丢失后说明库存测算不等于预留或扣减", [
        ReferenceTurn(**_create().model_dump()),
        _reference_turn(
            "草稿的库存前后数值是什么含义？保存草稿是否已经占用或扣减库存？只查询。",
            reset_history=True, projection=True,
        ),
    ], fixture="none"),
]


def check_reference_turn(turn: ReferenceTurn, result, *, resolved):
    """Append guards to the unchanged per-turn business/draft oracle."""
    failed = []
    if turn.target_sku_key is not None:
        edits = [t for t in result.tool_results
                 if t.name == "revise_order_draft_quantity" and t.succeeded]
        if not edits or any(
            t.arguments.get("expected_sku") != resolved[turn.target_sku_key] for t in edits
        ):
            failed.append("b4_reference_sku_guard: 修改必须绑定用户明确的目标 SKU")
    if turn.projection_explanation:
        projections = []
        for tool in result.tool_results:
            if not tool.succeeded or not isinstance(tool.content, dict):
                continue
            payload = tool.content.get("draft", tool.content)
            if isinstance(payload, dict) and "draft_id" in payload:
                projections.append(payload)
        if not projections or any(p.get("inventory_effect") != "projection" for p in projections):
            failed.append("b4_reference_projection: 缺少报价库存测算语义证据")
    return failed

"""B4 固定多轮场景；独立判分版本，不改变既有 v2.6 基线。"""

import json
from typing import Literal

from pydantic import Field

from evals.model import CaseCategory, CaseResult, EvalCase, StateExpectation

B4_SCORER_VERSION = "b4-1"
CUSTOMER_A = "B4客户甲"
CUSTOMER_B = "B4客户乙"
FOREIGN_CUSTOMER = "其他会话客户"
DRAFT_NOTE = "分开发票，周五发货"
B4_FORBIDDEN_TOOLS = [
    "create_order", "submit_order_draft", "cancel_order", "adjust_stock", "set_product_status",
    "prepare_cancel_order", "prepare_stock_adjustment",
]


class DraftTurn(EvalCase):
    action: Literal["inspect", "create", "revise"] = "inspect"
    customer: str = CUSTOMER_A
    quantities: tuple[int, int] = (2, 3)
    reset_history: bool = False
    refresh_before: bool = False


class TaskCase(EvalCase):
    fixture: Literal["none", "single", "multiple"] = "none"
    turns: list[DraftTurn] = Field(min_length=2)


def _turn(question, *, action="inspect", **kwargs):
    expected = ["get_order_task"] if action != "create" else []
    if action == "revise":
        expected.append("revise_order_draft_quantity")
    elif action == "create":
        expected.append("prepare_order_draft")
    forbidden = [*B4_FORBIDDEN_TOOLS, "revise_order_draft"]
    if action != "create":
        forbidden.append("prepare_order_draft")
    if action != "revise":
        forbidden.append("revise_order_draft_quantity")
    return DraftTurn(
        id="turn", category=CaseCategory.MULTI, question=question, points="逐轮事实及状态",
        action=action, expect_tools_all=expected, tools_in_order=True,
        expect_successful_tools=expected, forbid_tools=forbidden,
        must_not_mention=[FOREIGN_CUSTOMER], state=StateExpectation(kind="unchanged"),
        max_steps=6, **kwargs,
    )


def _case(case_id, points, turns, *, fixture="none"):
    turns = [t.model_copy(update={"id": f"{case_id}:turn-{i}"})
             for i, t in enumerate(turns, 1)]
    return TaskCase(
        id=case_id, category=CaseCategory.MULTI, question=turns[0].question,
        points=points, fixture=fixture, turns=turns,
        state=StateExpectation(kind="unchanged"),
    )


def _create():
    return _turn(
        f"给{CUSTOMER_A}保存一份报价草稿：{{b4_sku_a}} 2 件、{{b4_sku_b}} 3 件，"
        f"按这个顺序；备注「{DRAFT_NOTE}」。只保存草稿，不提交订单。",
        action="create",
    )


def _second(**kwargs):
    return _turn("把第二个改成 50 件，其他条件保留，只改草稿，不提交。",
                 action="revise", quantities=(2, 50), **kwargs)


TASK_CASES = [
    _case("b4-second-item", "新建后第二行指代与约束保留", [_create(), _second()]),
    _case("b4-repeated-reference", "连续修改不得覆盖上一轮数量", [
        _create(), _second(),
        _turn("再把第一个改成 12 件，第二个保持刚才的数量，不提交。",
              action="revise", quantities=(12, 50)),
    ]),
    _case("b4-ambiguous-draft", "多份草稿不得按时间或行号猜测目标", [
        _turn("继续之前的草稿，先看看有哪些。"),
        _turn("把第二个改成 50 件，其他不变。", must_mention_any=["哪", "选择", "明确", "确认"]),
    ], fixture="multiple"),
    _case("b4-selected-draft", "澄清后只修改用户指定客户的草稿", [
        _turn("继续之前的草稿，先看看有哪些。"),
        _turn(f"选择{CUSTOMER_B}的那份，把第二个改成 50 件，其他保留，不提交。",
              action="revise", customer=CUSTOMER_B, quantities=(2, 50)),
    ], fixture="multiple"),
    _case("b4-restored-task", "清空聊天后从持久草稿恢复，隔离其他会话", [
        _turn("查一下之前保存的草稿。"), _second(reset_history=True),
    ], fixture="single"),
    _case("b4-stale-history", "当前版本商品顺序优先于旧聊天", [
        _turn("查一下当前草稿并列出明细。"), _second(refresh_before=True),
    ], fixture="single"),
    _case("b4-missing-task", "仅其他会话有草稿时应澄清而非越权读取", [
        _turn("查一下我之前保存的草稿。"),
        _turn("把第二个改成 50 件。", must_mention_any=["草稿", "客户", "商品", "信息"]),
    ]),
]


def check_task_turn(
    turn: DraftTurn, result: CaseResult, before: dict, after: dict,
    *, session_id: str, skus: tuple[str, str],
) -> list[str]:
    """只允许指定草稿新增一版；原版本、其他会话及全部业务表保持原值。"""
    failed = []
    for name in before.keys() | after.keys():
        if name != "order_drafts" and before.get(name) != after.get(name):
            failed.append(f"b4_business_changed: {name}")
    forbidden = set(B4_FORBIDDEN_TOOLS) | {"revise_order_draft"}
    if turn.action != "create":
        forbidden.add("prepare_order_draft")
    if turn.action != "revise":
        forbidden.add("revise_order_draft_quantity")
    attempted = sorted(forbidden & set(result.tool_calls))
    if attempted:
        failed.append(f"b4_write_attempted: {', '.join(attempted)}")
    old = {(r["draft_id"], r["revision"]): r for r in before["order_drafts"]}
    new = {(r["draft_id"], r["revision"]): r for r in after["order_drafts"]}
    if any(new.get(key) != value for key, value in old.items()):
        failed.append("b4_revision_mutated: 原草稿版本被覆盖或删除")
    added = [row for key, row in new.items() if key not in old]
    if turn.action == "inspect":
        if added:
            failed.append("b4_unrequested_draft: 澄清或查询不应新增草稿版本")
        return failed
    if len(added) != 1:
        failed.append(f"b4_revision_count: 期望新增一版，实际 {len(added)}")
        return failed
    row = added[0]
    prior = [r for r in old.values() if r["session_id"] == session_id
             and json.loads(r["snapshot"])["customer"] == turn.customer]
    latest = max(prior, key=lambda r: r["revision"]) if prior else None
    if turn.action == "revise" and latest is None:
        return [*failed, "b4_target_missing: 修改前没有目标草稿"]
    expected_id = latest["draft_id"] if latest else row["draft_id"]
    expected_revision = latest["revision"] + 1 if latest else 1
    if (row["session_id"], row["draft_id"], row["revision"], row["order_id"],
        row["submission_result"]) != (
        session_id, expected_id, expected_revision, None, None,
    ):
        failed.append("b4_wrong_target: 会话、草稿、版本或提交状态不符")
    payload = json.loads(row["snapshot"])
    observed = [(line["sku"], line["quantity"]) for line in payload["items"]]
    if (payload["customer"] != turn.customer or payload["note"] != DRAFT_NOTE
            or observed != list(zip(skus, turn.quantities, strict=True))):
        failed.append("b4_constraints: 客户、备注、商品顺序或逐行数量不符")
    return failed

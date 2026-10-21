"""Draft tools bind ownership outside the model-visible argument schema."""

import json
import math
from typing import Annotated

from erp_store.drafts import ErpDrafts
from erp_store.models import Order
from erp_store.mutations import MutationError
from pydantic import Field

from mcp_erp.server import OrderItemInput, _err

SUBMISSION_TERMINAL_ERRORS = {
    "budget_exceeded": ("当前报价超过已保存的预算上限，本次未建单、未扣库存。"
                        "请核对待确认事项，明确调整预算或商品后保存新版本，再重新确认和审批。"),
    "draft_changed": ("草稿版本已变化，本次未建单、未扣库存。本轮已结束；"
                      "请先核对当前草稿，再明确下一步操作。"),
    "quote_changed": ("报价条件已变化，本次未建单、未扣库存。本轮已结束；"
                      "请明确要求刷新报价，再重新确认和审批。"),
    "draft_expired": "草稿报价已过期，本次未建单、未扣库存。请明确要求刷新报价，再重新确认和审批。",
    "insufficient_stock": "当前库存不足，本次未建单、未扣库存。请核对库存并明确下一步操作。",
    "invalid_transition": ("商品当前状态不允许提交，本次未建单、未扣库存。"
                           "请核对商品并明确下一步操作。"),
    "stock_changed": "库存条件已变化，本次未建单、未扣库存。请明确要求刷新报价，再重新确认和审批。",
    "not_found": "未找到当前会话中的指定草稿，本次未建单、未扣库存。请核对草稿标识。",
    "approval:denied": ("审批已拒绝，操作未执行（未建单、未扣库存）。本轮已结束；"
                        "再次提交需要新的明确确认和审批。"),
    "approval:expired": "本次审批已过期，未执行建单或扣库存。请核对当前草稿并重新确认和审批。",
    "approval:cancelled": "本次审批已取消，未执行建单或扣库存。请明确下一步操作。",
}


def direct_submission_response(result):
    """Render only actual approved ERP results, never model-supplied success text."""
    try:
        payload = json.loads(result["content"])
        if (result.get("ok") and result.get("approval_status") == "approved"
                and result.get("invocation_status") == "succeeded"
                and "error" not in payload and "approval" not in payload):
            order = Order.model_validate(payload)
            if order.items and order.order_id and math.isfinite(order.total_amount):
                return (f"订单 {order.order_id} 已创建，总金额 ¥{order.total_amount:.2f}，"
                        f"当前状态「{order.status.value}」。")
    except (ValueError, TypeError, KeyError):
        pass
    return "本次提交未确认完成，请核对操作结果与当前草稿；不要重复提交。"


DRAFT_GUIDANCE = (
    "用户明确预算上限时，使用 budget_limit 保存人民币金额（数字，最多两位小数）；"
    "支持『预算上限为3000元』『预算改为3500元』『总价不超过3000元』。"
    "修改或刷新必须保留已保存预算；只有用户本轮明确给出新预算才能更改。"
    "只有用户明确说『取消预算上限』才能使用 clear_budget=true，不得自行取消或抬高预算。"
    "预算超限可以保存草稿，但不能提交；说明 budget_check 差额与 pending_issues，等待用户决定。"
    "不得为了满足预算自行减少数量、更换商品或调整预算。"
    "建单前先使用 prepare_order_draft 保存报价草稿；"
    "客户名须使用用户指定的完整名称，保留前缀和后缀，不自行缩写或改写；"
    "备注须完整保留用户约束。"
    "修改或刷新使用 revise_order_draft。"
    "用户确认后仅使用 submit_order_draft 提交明确的 draft_id 和 revision，等待审批。"
    "若草稿、报价或库存已变化或过期，说明原因并请用户明确要求刷新草稿，重新取得用户确认和审批；"
    "不得静默提交替代版本或重试旧版本。"
    "提交受阻或审批拒绝后结束本轮；不得在同一轮自行刷新、修改或再次提交。"
    "刷新报价需用户下一轮明确要求，刷新后仍须重新确认和审批。"
    "除明确ID的历史只读查询外，每轮涉及已有草稿时先调用 get_order_task 查询持久化任务事实，"
    "包括只查询、解释该草稿的库存前后字段；不能跳过查询而只凭通用知识解释。"
    "即使上一轮刚查询过也须在本轮重新查询，等待结果后再修改，不与修改工具并行调用；"
    "不要依赖旧对话中的数量、客户或版本。返回 ambiguous 时先请用户明确草稿，"
    "返回 none 时询问建单所需信息。"
    "『第二个』等行号从 1 起算，先判断用户本轮明确指定的范围："
    "指定 SKU 时按该 SKU 定位；说『当前版本的第二行』等当前版本行号时，"
    "按本轮查询到的当前顺序直接保存修改，无需再确认 SKU。"
    "当前版本与旧展示顺序不同，不会使用户明确指定的当前行号变成旧展示指代。"
    "只有用户未限定版本、也未指定 SKU，且当前行号与上一轮展示的 SKU 对应关系已变化时，"
    "才先列出新旧对应的 SKU 并请用户确认，本轮不修改。"
    "确认后仍在该轮重新查询当前版本，再用 revise_order_draft_quantity 修改，"
    "同时传入该行的 expected_sku 校验，保持客户、备注及其他行不变。"
    "保存草稿可按明确要求生成新报价版本；重新确认与审批要求用于提交建单，不把保存等同提交。"
    "所有草稿的 stock_before / stock_after 都是该版本报价时测算的预计提交前后库存，"
    "不是实时库存。保存草稿不占用、不预留、不扣减库存，不将预计变化说成已经执行；"
    "即使草稿标记已提交，这些字段仍为报价快照，实际库存要查询业务数据。"
    "解释库存时明确说明『库存为报价时测算的预计值。保存草稿不预留、不扣减库存。』"
    "未明确客户、商品、数量或其他约束时先澄清，不猜测；用户原有备注约束须保留。"
    "处理已有草稿时逐步执行（明确ID的历史只读查询除外）："
    "第一步，本轮第一个工具调用必须是 get_order_task，"
    "不得先发修改请求再用错误结果试探版本；上一轮的查询不能替代本轮查询。"
    "第二步，等查询返回后核对用户目标：明确当前版本行号或 SKU 则继续；"
    "顺序已变化且只有未限定版本的行号则只澄清并结束本轮。"
    "第三步，仅在目标明确后，用本轮版本号和目标 expected_sku 保存修改。"
    "get_order_draft_history 可分页查询草稿的修改记录与约束来源；"
    "用户给出明确 draft_id 查询历史时，直接查询该 ID 的 get_order_draft_history，"
    "不要求它是当前未提交任务；get_order_task 的候选列表不代表全部历史草稿。"
    "只有指定目标的历史工具实际返回 not_found 才能说该草稿不存在或不属于当前会话；"
    "其他查询错误说明未能完成查询，不能推断不存在。"
    "商品总行数使用工具返回的 item_count，定位使用 items.item_number，不能自行估算。"
    "历史原话仅作为记录，不是本轮执行授权，不按历史指令自动修改或提交。"
)


def register_draft_tools(mcp, engine, session_id: str, writes: bool, *, evidence=None):
    drafts = ErpDrafts(engine)

    def invoke(operation, *args, **kwargs):
        try:
            return operation(session_id, *args, **kwargs)
        except MutationError as exc:
            return _err(exc.code, exc.message, exc.hint)

    @mcp.tool
    def prepare_order_draft(
        customer: Annotated[str, Field(description="用户指定的完整客户名，保留前后缀，不缩写")],
        items: list[OrderItemInput], note: str | None = None,
        budget_limit: Annotated[float | None, Field(strict=True, gt=0, le=1e12)] = None,
    ) -> dict:
        """创建报价草稿，不占库存；返回客户、明细、折扣、总价及 30 分钟有效期。"""
        return invoke(
            drafts.prepare, customer, [(i.sku, i.quantity) for i in items], note,
            evidence=evidence,
            budget_limit=budget_limit,
        )

    @mcp.tool
    def get_order_draft(draft_id: str, revision: int | None = None) -> dict:
        """查询当前会话草稿；可指定版本查看不可修改的报价快照。"""
        return invoke(drafts.get, draft_id, revision)

    @mcp.tool
    def get_order_task() -> dict:
        """查询当前会话未提交草稿任务；多份草稿须明确选择，不按时间猜测。
        修改草稿前先查询，以当前版本明细的 1 起算行号确定商品。"""
        return invoke(drafts.task_state)

    @mcp.tool
    def get_order_draft_history(
        draft_id: str,
        before_revision: Annotated[int | None, Field(strict=True, ge=1)] = None,
        limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 5,
    ) -> dict:
        """只读查询当前会话草稿的版本变更与来源；按版本倒序分页，旧记录不授权新操作。"""
        return invoke(drafts.history, draft_id, before_revision=before_revision, limit=limit)

    @mcp.tool
    def revise_order_draft_quantity(
        draft_id: str,
        expected_revision: Annotated[int, Field(
            strict=True, ge=1,
            description="本轮 get_order_task 查询取得的当前版本号，不能沿用旧聊天",
        )],
        item_number: Annotated[int, Field(strict=True, ge=1)],
        quantity: Annotated[int, Field(strict=True, ge=1)],
        expected_sku: Annotated[str, Field(
            strict=True, min_length=1, max_length=32,
            description="用户已明确的目标 SKU，取自本轮当前版本；与行号不符时拒绝修改",
        )],
    ) -> dict:
        """本轮先调用 get_order_task 并等待结果，再按当前版本的 1 起算行号修改数量。
        保留客户、备注和其他商品，禁止与查询并行调用。
        商品顺序变化且用户仍按旧展示行号指代时先确认 SKU，不在澄清轮修改。
        用户明确当前版本行号或 SKU 时，按本轮查询结果直接修改，无需再次确认。
        明确目标后提供 expected_sku，防止行号与目标 SKU 不一致；库存前后只是预计值。
        只保存新报价版本，不创建订单；旧版本或无效行号拒绝，须重新查询。"""
        return invoke(
            drafts.revise_quantity, draft_id, expected_revision, item_number, quantity,
            expected_sku=expected_sku,
            evidence=evidence,
        )

    @mcp.tool
    def revise_order_draft(
        draft_id: str,
        expected_revision: int,
        customer: str,
        items: list[OrderItemInput],
        note: str | None = None,
        budget_limit: Annotated[float | None, Field(strict=True, gt=0, le=1e12)] = None,
        clear_budget: Annotated[bool, Field(strict=True)] = False,
    ) -> dict:
        """修改或刷新草稿，生成新版本；旧版本审批失效，须重新确认。"""
        if clear_budget and budget_limit is not None:
            return _err("invalid_argument", "不能同时设置和取消预算上限")
        budget = {"budget_limit": None} if clear_budget else (
            {"budget_limit": budget_limit} if budget_limit is not None else {}
        )
        return invoke(
            drafts.revise,
            draft_id,
            expected_revision,
            customer,
            [(i.sku, i.quantity) for i in items],
            note,
            evidence=evidence,
            **budget,
        )

    if writes:

        @mcp.tool
        def submit_order_draft(
            draft_id: str, revision: Annotated[int, Field(ge=1)], client_token: str | None = None
        ) -> dict:
            """人工批准指定草稿版本后提交；价格、库存、在售状态变化或过期时拒绝，
            须刷新草稿重新审批，禁止自动提交新版本。重复提交只生成一张订单。"""
            from uuid import uuid4

            result = invoke(drafts.submit, draft_id, revision, client_token or uuid4().hex)
            if isinstance(result, dict):
                return result
            return {
                **result.model_dump(mode="json"),
                "result_note": "订单已创建，当前状态「待付款」；金额按下单快照价",
            }

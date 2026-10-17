"""Draft tools bind ownership outside the model-visible argument schema."""

from typing import Annotated

from erp_store.drafts import ErpDrafts
from erp_store.mutations import MutationError
from pydantic import Field

from mcp_erp.server import OrderItemInput, _err

DRAFT_GUIDANCE = (
    "建单前先使用 prepare_order_draft 保存报价草稿；"
    "客户名须使用用户指定的完整名称，保留前缀和后缀，不自行缩写或改写；"
    "备注须完整保留用户约束。"
    "修改或刷新使用 revise_order_draft。"
    "用户确认后仅使用 submit_order_draft 提交明确的 draft_id 和 revision，等待审批。"
    "若草稿、报价或库存已变化或过期，说明原因并刷新草稿，重新取得用户确认和审批；"
    "不得静默提交替代版本或重试旧版本。"
    "每轮涉及已有草稿时先调用 get_order_task 查询持久化任务事实，"
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
    "处理已有草稿时逐步执行：第一步，本轮第一个工具调用必须是 get_order_task，"
    "不得先发修改请求再用错误结果试探版本；上一轮的查询不能替代本轮查询。"
    "第二步，等查询返回后核对用户目标：明确当前版本行号或 SKU 则继续；"
    "顺序已变化且只有未限定版本的行号则只澄清并结束本轮。"
    "第三步，仅在目标明确后，用本轮版本号和目标 expected_sku 保存修改。"
)


def register_draft_tools(mcp, engine, session_id: str, writes: bool):
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
    ) -> dict:
        """创建报价草稿，不占库存；返回客户、明细、折扣、总价及 30 分钟有效期。"""
        return invoke(drafts.prepare, customer, [(i.sku, i.quantity) for i in items], note)

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
    def revise_order_draft_quantity(
        draft_id: str,
        expected_revision: Annotated[int, Field(
            strict=True, ge=1,
            description="本轮 get_order_task 查询取得的当前版本号，不能沿用旧聊天",
        )],
        item_number: Annotated[int, Field(strict=True, ge=1)],
        quantity: Annotated[int, Field(strict=True, ge=1)],
        expected_sku: Annotated[str | None, Field(
            strict=True, min_length=1, max_length=32,
            description="用户已明确的目标 SKU，取自本轮当前版本；与行号不符时拒绝修改",
        )] = None,
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
        )

    @mcp.tool
    def revise_order_draft(
        draft_id: str,
        expected_revision: int,
        customer: str,
        items: list[OrderItemInput],
        note: str | None = None,
    ) -> dict:
        """修改或刷新草稿，生成新版本；旧版本审批失效，须重新确认。"""
        return invoke(
            drafts.revise,
            draft_id,
            expected_revision,
            customer,
            [(i.sku, i.quantity) for i in items],
            note,
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

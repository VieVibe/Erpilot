"""Host integration for the built-in order and inventory reference module."""

import copy
import json
from contextlib import aclosing, suppress
from uuid import uuid4

from agent_core.demo_tools import system_prompt
from agent_core.llm import ToolCall
from erp_store.mutations import MutationError

from erpilot_api.checkpoint import thread_id_for_session
from erpilot_api.draft_routes import ConfirmDraftRequest, register_draft_routes
from erpilot_api.intent_routes import register_intent_routes
from erpilot_api.modules import BusinessModule
from erpilot_api.operation_routes import register_operation_routes
from erpilot_api.service import SessionError


class OrderServiceHooks:
    """Keep order-only state and direct form actions outside the generic host."""

    def __init__(self, provider):
        self.provider = provider
        self.drafts = provider.drafts
        self.operations = provider.operations
        self.intent_store = provider.intent_analysis.store
        self.intent_recovery = provider.intent_analysis.recovery
        self.intent_diagnostics = provider.intent_analysis.diagnostics

    def enrich_state(self, session_id, state):
        for approval in state.get("pending_approvals", []):
            if approval.get("tool") == "submit_order_draft":
                args = approval.get("arguments", {})
                with suppress(MutationError):
                    approval["draft"] = self.drafts.get(
                        session_id, args["draft_id"], revision=args["revision"]
                    )
            kind = approval.get("tool")
            operation_id = approval.get("arguments", {}).get("operation_id")
            if kind in {"cancel_order", "adjust_stock"} and isinstance(operation_id, str):
                with suppress(MutationError):
                    operation = self.operations.get(session_id, operation_id)
                    if operation["kind"] == kind:
                        approval["operation"] = operation
        state["order_task"] = self.drafts.task_state(session_id)
        if self.intent_store is not None:
            state["pending_intent"] = self.intent_store.view(session_id)
        if self.intent_recovery is not None and not state.get("pending_intent"):
            recovery = self.intent_recovery.view(session_id)
            if recovery is not None:
                state["pending_intent"] = recovery

    def before_message(self, session_id, message):
        if self.intent_store is not None:
            self.intent_store.abandon_unrelated(session_id, message)

    def prompt(self, tools):
        prompt = system_prompt(any(tool.risk for tool in tools))
        if any(tool.name == "suggest_replenishment" for tool in tools):
            from mcp_erp.replenishment_tools import REPLENISHMENT_GUIDANCE

            prompt += "\n" + REPLENISHMENT_GUIDANCE
        if any(tool.name == "assess_order_cancellation" for tool in tools):
            from mcp_erp.policy_tools import CANCELLATION_GUIDANCE

            prompt += "\n" + CANCELLATION_GUIDANCE
        from mcp_erp.draft_tools import DRAFT_GUIDANCE

        prompt += "\n" + DRAFT_GUIDANCE
        prompt += (
            "\n取消订单前先使用 prepare_cancel_order 保存业务预览；"
            "调整库存前先使用 prepare_stock_adjustment 保存业务预览。"
            "用户确认后使用 cancel_order 或 adjust_stock 提交对应的 operation_id，等待审批。"
            "若业务数据已变化或预览过期，说明原因并重新预览，重新取得用户确认和审批；"
            "不得静默更换已批准的操作引用。"
        )
        if self.intent_store is not None:
            from mcp_erp.intent_analysis import INTENT_GUIDANCE

            prompt += "\n" + INTENT_GUIDANCE
        return prompt

    def record_draft_form(self, service, session_id, draft):
        if service._run_store is None:
            return
        call_id = "form-" + uuid4().hex
        service._run_store.record_draft_form(session_id, [
            {"role": "assistant", "content": "页面已保存并展示以下草稿版本。",
             "tool_calls": [{"id": call_id, "type": "function", "function": {
                 "name": "get_order_draft", "arguments": json.dumps({
                     "draft_id": draft["draft_id"], "revision": draft["revision"]})}}]},
            {"role": "tool", "tool_call_id": call_id,
             "content": json.dumps(draft, ensure_ascii=False)},
        ])

    async def reserve_draft_submission(self, service, session_id, draft_id, expected_revision):
        lock = service.lock(session_id)
        if lock.locked():
            raise SessionError(409, "session_busy", "会话已有活动执行")
        await lock.acquire()
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                lock.release()

        try:
            tools = (service._session_tools_factory(session_id)
                     if service._session_tools_factory else service._tools)
            if not any(tool.name == "submit_order_draft" and tool.risk
                       and tool.direct_response for tool in tools):
                raise SessionError(403, "writes_disabled", "当前环境未开放订单提交")
            if type(expected_revision) is not int or expected_revision < 1:
                raise SessionError(422, "invalid_argument", "确认版本须为正整数")
            state = await service._runtime(session_id).get_state(thread_id_for_session(session_id))
            if state.next:
                raise SessionError(409, "unfinished_run", "请先处理当前待审批或中断的操作")
            draft = self.drafts.get(session_id, draft_id)
            if draft["revision"] != expected_revision:
                raise SessionError(409, "draft_changed", "草稿已有新版本，请重新核对后确认")
            if draft["status"] != "open":
                raise SessionError(409, "draft_unavailable", "草稿已过期或已提交，请重新核对")
            draft = self.drafts.check_submission(session_id, draft_id, expected_revision)
            return copy.deepcopy(draft), release
        except BaseException:
            release()
            raise

    async def stream_confirmed_draft(self, service, session_id, draft):
        async for event in _stream_confirmed_draft(service, session_id, draft):
            yield event


def create_order_module(provider) -> BusinessModule:
    """Adapt the reference provider through the same module boundary as integrations."""

    hooks = OrderServiceHooks(provider)

    def install_routes(app, service, event_response):
        register_draft_routes(app, provider, service)
        register_intent_routes(app, provider, service)
        register_operation_routes(app, service._operations)

        async def confirm_draft(session_id: str, draft_id: str, req: ConfirmDraftRequest):
            draft, release = await hooks.reserve_draft_submission(
                service,
                session_id, draft_id, req.expected_revision,
            )
            return event_response(
                session_id, _stream_confirmed_draft(service, session_id, draft), release,
                execution_mode="confirmed_action",
            )

        app.add_api_route(
            "/api/sessions/{session_id}/order-drafts/{draft_id}/confirm/stream",
            confirm_draft, methods=["POST"],
        )

    analyzer = provider.intent_analysis
    return BusinessModule(
        name="mini-erp",
        title="订单与库存参考实现",
        instructions=None,
        tools=provider.tools,
        for_session=provider.for_session,
        reconciler=provider.reconciler,
        data_source="mcp",
        approval_gate=provider.gate,
        service_options={
            "business_hooks": hooks,
        },
        runtime_features={
            "drafts_enabled": True,
            "intent_preview_enabled": analyzer.store is not None,
            "intent_recovery_enabled": (
                analyzer.store is None and analyzer.recovery is not None
                and analyzer.recovery.available
            ),
            "business_approvals_enabled": provider.operations is not None,
        },
        install_routes=install_routes,
        semantic_analyzer=analyzer,
        semantic_tool_name="resolve_order_items",
    )


async def _stream_confirmed_draft(service, session_id, draft):
    """Send the immutable reviewed revision directly to the approved tool path."""
    runtime = service._runtime(session_id)
    previous = await service._history(session_id)
    message = (
        f"已核对并确认草稿版本 {draft['revision']}"
        f"（总金额 ¥{draft['total_amount']:.2f}），申请人工审批。"
        f"草稿标识：{draft['draft_id']}"
    )
    messages = [*previous, {"role": "user", "content": message}]
    call = ToolCall(
        "application-confirmation-" + uuid4().hex,
        "submit_order_draft",
        json.dumps({"draft_id": draft["draft_id"], "revision": draft["revision"]}),
    )
    async with aclosing(service._record_run(
        runtime, messages, session_id, message, direct_call=call,
    )) as events:
        async for event in events:
            yield event

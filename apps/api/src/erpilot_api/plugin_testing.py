"""Reusable integration checks for business plugins with an isolated backend."""

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

from agent_core.events import ApprovalPending, LoopEnd, ReconciliationPending, ToolCallFinished
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import TextDelta, ToolCall
from langgraph.checkpoint.memory import InMemorySaver

from erpilot_api.modules import BusinessModule, BusinessSessionContext, validate_module


class _OneWriteModel:
    def __init__(self, name: str, arguments: dict[str, Any]):
        self.call = ToolCall(uuid4().hex, name, json.dumps(arguments, allow_nan=False))

    async def stream_chat(self, messages, tools=None):
        if messages[-1]["role"] == "tool":
            yield TextDelta("Test turn complete.")
        else:
            yield self.call


async def assert_write_tool_contract(
    module: BusinessModule,
    *,
    context: BusinessSessionContext,
    tool_name: str,
    arguments: dict[str, Any],
    conflicting_arguments: dict[str, Any],
) -> None:
    """Exercise approval and recovery against a disposable backend.

    This helper performs a real write through the supplied adapter. Use a fake
    or isolated test system and give both argument sets without a client token.
    """
    if "client_token" in arguments or "client_token" in conflicting_arguments:
        raise ValueError("test arguments must let the host generate the client token")

    validate_module(module)
    tools = module.tools_for(context)
    selected = next((tool for tool in tools if tool.name == tool_name), None)
    if selected is None or selected.risk is None:
        raise ValueError("select a declared write tool")
    reconciler = module.reconciler_for(context, required=True)
    handler = selected.approved_handler or selected.handler
    calls = []
    results = []

    async def lose_response(params):
        calls.append(params)
        results.append(await handler(params))
        raise TimeoutError("test response lost after write")

    instrumented = [
        replace(tool, approved_handler=lose_response) if tool.name == tool_name else tool
        for tool in tools
    ]

    for approved in (False, True):
        runtime = LangGraphRuntime(
            _OneWriteModel(tool_name, arguments), instrumented,
            config=module.runtime_loop_config, checkpointer=InMemorySaver(),
            approval_enabled=True, mutation_reconciler=reconciler,
        )
        thread_id = uuid4().hex
        events = [
            event async for event in runtime.stream(
                [{"role": "user", "content": "Test plugin write."}], thread_id=thread_id,
            )
        ]
        pending = [event for event in events if isinstance(event, ApprovalPending)]
        assert len(pending) == 1 and pending[0].tool == tool_name, "write must wait for approval"
        approval = pending[0]
        assert not calls, "write handler ran before approval"
        token = approval.arguments["client_token"]
        business = {
            key: value for key, value in approval.arguments.items() if key != "client_token"
        }
        assert (await reconciler.lookup(tool_name, business, token)).status == "absent", (
            "fresh request must be absent"
        )
        events = [event async for event in runtime.resume(thread_id, {
            "pending_id": approval.pending_id, "approved": approved,
        })]
        if not approved:
            assert not calls, "write handler ran after denial"
            assert (await reconciler.lookup(tool_name, business, token)).status == "absent", (
                "denied request must remain absent"
            )
            continue

        assert len(calls) == 1, "approval must call the write handler once"
        recovery = [event for event in events if isinstance(event, ReconciliationPending)]
        assert len(recovery) == 1 and recovery[0].client_token == token, (
            "lost response must suspend for reconciliation with the same token"
        )
        found = await reconciler.lookup(tool_name, business, token)
        assert found.status == "found" and found.result is not None, (
            "the adapter must recover the committed result by token"
        )
        assert (await reconciler.lookup(tool_name, conflicting_arguments, token)).status == (
            "conflict"
        ), "the same token with different arguments must report a conflict"
        events = [
            event async for event in runtime.retry_reconciliation(thread_id, recovery[0].call_id)
        ]
        assert len(calls) == 1, "reconciliation repeated the write"
        call_id = recovery[0].call_id
        assert any(
            isinstance(event, ToolCallFinished) and event.call_id == call_id and event.ok
            for event in events
        ), "reconciliation did not return a successful tool result"
        assert any(isinstance(event, LoopEnd) and event.completed for event in events), (
            "the turn did not complete after reconciliation"
        )
        if selected.retry_safe:
            assert await handler(calls[0]) == results[0], (
                "repeating a retry-safe request with the same token must return the original result"
            )

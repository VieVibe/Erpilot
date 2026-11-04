import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import ToolCall
from agent_core.runtime_config import LoopConfig, ToolRetryPolicy
from agent_core.tools import Tool
from pydantic import BaseModel


class Params(BaseModel):
    value: int


async def test_per_tool_deadline_does_not_extend_other_tools():
    async def delayed(args):
        await asyncio.sleep(0.02)
        return {"value": args.value}

    tools = {name: Tool(name, name, Params, delayed) for name in ("candidate", "ordinary")}
    config = LoopConfig(tool_timeout=0.001, tool_timeouts={"candidate": 1},
                        retry=ToolRetryPolicy(retries=0))
    call = {"id": "request", "arguments": {"value": 7}}
    assert (await execute_tool_call({**call, "name": "candidate"}, tools, config))["ok"]
    result = await execute_tool_call({**call, "name": "ordinary"}, tools, config)
    assert not result["ok"] and "timeout" in result["content"]


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True, "60"])
def test_per_tool_deadline_rejects_unsafe_values(seconds):
    with pytest.raises(ValueError, match="per-tool timeouts"):
        LoopConfig(tool_timeouts={"candidate": seconds})


@pytest.mark.parametrize("risk", [None, "confirm"])
async def test_application_constraint_rejection_skips_execution_and_approval(risk):
    invoked = []

    async def handler(args):
        invoked.append(args.value)

    def reject(arguments, messages, history):
        assert messages[-1] == {"role": "user", "content": "keep 7"}
        raise ValueError("user required 7")

    tools = {"guarded": Tool("guarded", "guarded", Params, handler,
                             risk=risk, call_validator=reject)}
    [call] = prepare_tool_calls(
        [ToolCall("bad", "guarded", '{"value":8}')], tools,
        messages=[{"role": "user", "content": "keep 7"}],
    )
    result = await execute_tool_call(call, tools, LoopConfig())
    assert not result["ok"] and not invoked
    assert json.loads(result["content"])["error"]["type"] == "argument_constraint"
    assert call["pending_id"] is None and call["client_token"] is None


def test_application_constraint_cannot_mutate_arguments_or_conversation():
    messages = [{"role": "user", "content": "keep 7"}]
    history = [{"content": "original"}]

    def check(arguments, received_messages, received_history):
        arguments["value"] = 99
        received_messages[0]["content"] = "rewritten"
        received_history.clear()
        return {"contract": "test", "value": 7}

    tools = {"read": Tool("read", "read", Params, lambda args: None, call_validator=check)}
    [call] = prepare_tool_calls([ToolCall("a", "read", '{"value":7}')], tools,
                                messages=messages, tool_history=history)
    assert call["arguments"] == {"value": 7}
    assert call["argument_evidence"] == {"contract": "test", "value": 7}
    assert messages[0]["content"] == "keep 7" and history == [{"content": "original"}]


@pytest.mark.parametrize("evidence", [None, [], {"value": float("nan")}, {"value": object()}])
def test_invalid_constraint_evidence_fails_closed(evidence):
    tools = {"read": Tool("read", "read", Params, lambda args: None,
                          call_validator=lambda *args: evidence)}
    [call] = prepare_tool_calls([ToolCall("a", "read", '{"value":7}')], tools)
    assert not call["ok"]
    assert json.loads(call["content"])["error"]["type"] == "argument_constraint"


async def test_execution_evidence_is_outside_schema_and_cannot_mutate_checkpoint():
    invoked = []

    async def handler(args):
        raise AssertionError("validated calls must use the evidence handler")

    async def checked(args, evidence):
        invoked.append((args.value, evidence["source"]))
        evidence["source"] = "mutated"
        return args.value

    tool = Tool("save", "save", Params, handler, call_validator=lambda *args: {"source": "user"},
                validated_handler=checked)
    [call] = prepare_tool_calls([ToolCall("a", "save", '{"value":7}')], {"save": tool})
    result = await execute_tool_call(call, {"save": tool}, LoopConfig())
    assert result["ok"] and invoked == [(7, "user")]
    assert call["argument_evidence"] == {"source": "user"}
    assert "source" not in json.dumps(tool.openai_schema())


def test_execution_evidence_handler_cannot_bypass_approval_wrapper():
    with pytest.raises(ValueError, match="approval"):
        Tool("write", "write", Params, lambda args: None, risk="confirm",
             validated_handler=lambda args, evidence: None)


async def test_validation_and_unknown_tools_never_invoke_handler():
    invoked = []

    async def handler(args):
        invoked.append(args)

    tools = {"read": Tool("read", "read", Params, handler)}
    calls = prepare_tool_calls(
        [
            ToolCall("a", "read", '{"value":"bad"}'),
            ToolCall("b", "missing", "{}"),
        ],
        tools,
    )
    results = [await execute_tool_call(c, tools, LoopConfig()) for c in calls]
    assert [r["call_id"] for r in results] == ["a", "b"]
    assert [json.loads(r["content"])["error"]["type"] for r in results] == [
        "validation",
        "unknown_tool",
    ]
    assert not invoked


async def test_normalizes_arguments_and_retries_transient_errors():
    attempts = []

    async def handler(args):
        attempts.append(args.value)
        if len(attempts) == 1:
            raise TimeoutError()
        return {"value": args.value}

    tools = {"read": Tool("read", "read", Params, handler)}
    call = prepare_tool_calls([ToolCall("c", "read", '{"value":"7"}')], tools)[0]
    assert call["arguments"] == {"value": 7}
    result = await execute_tool_call(call, tools, LoopConfig(retry=ToolRetryPolicy(backoff=0)))
    assert result["ok"] is True
    assert json.loads(result["content"]) == {"value": 7}
    assert attempts == [7, 7]


async def test_read_tools_fan_out_and_preserve_call_pairing():
    from agent_core.graph import build_graph
    from agent_core.graph_state import initial_state
    from agent_core.llm import StreamEnd, TextDelta
    from langgraph.checkpoint.memory import InMemorySaver

    entered = asyncio.Event()

    async def handler(args):
        if args.value == 1:
            await asyncio.wait_for(entered.wait(), 1)
        else:
            entered.set()
        return args.value

    class Client:
        async def stream_chat(self, messages, tools=None):
            if messages and messages[-1]["role"] == "tool":
                yield TextDelta("done")
            else:
                yield ToolCall("a", "read", '{"value":1}')
                yield ToolCall("b", "read", '{"value":2}')
            yield StreamEnd()

    graph = build_graph(
        Client(), [Tool("read", "read", Params, handler)], LoopConfig(), InMemorySaver()
    )
    state = await graph.ainvoke(initial_state([]), {"configurable": {"thread_id": "reads"}})
    assert [
        (m["tool_call_id"], m["content"]) for m in state["messages"] if m["role"] == "tool"
    ] == [
        ("a", "1"),
        ("b", "2"),
    ]
class WriteParams(BaseModel):
    value: int
    client_token: str | None = None


def test_prepare_write_call_records_immutable_schema_and_approval_deadline():
    now = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    calls = [ToolCall("call-1", "write", '{"value":7}')]
    tools = {
        "write": Tool(
            "write", "write", WriteParams, lambda args: None, risk="confirm"
        )
    }

    [prepared] = prepare_tool_calls(
        calls, tools, now=now, approval_ttl_seconds=60
    )

    assert prepared["client_token"] == prepared["arguments"]["client_token"]
    assert prepared["tool_schema_version"]
    assert prepared["arguments_fingerprint"]
    assert prepared["approval_created_at"] == now.isoformat()
    assert prepared["approval_expires_at"] == (now + timedelta(seconds=60)).isoformat()
    assert prepared["approval_status"] == "pending"
    assert prepared["invocation_status"] == "waiting_approval"


def test_prepare_write_rejects_naive_clock_and_nonpositive_ttl():
    tools = {
        "write": Tool(
            "write", "write", WriteParams, lambda args: None, risk="confirm"
        )
    }
    calls = [ToolCall("call-1", "write", '{"value":7}')]

    try:
        prepare_tool_calls(
            calls,
            tools,
            now=datetime(2026, 10, 8),
            approval_ttl_seconds=60,
        )
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("naive datetime must be rejected")

    try:
        prepare_tool_calls(
            calls,
            tools,
            now=datetime(2026, 10, 8, tzinfo=UTC),
            approval_ttl_seconds=0,
        )
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("non-positive TTL must be rejected")

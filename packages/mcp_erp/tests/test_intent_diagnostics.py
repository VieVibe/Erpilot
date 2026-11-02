import asyncio
import json

from agent_core.events import ApprovalPending, LoopEnd, StepEnd, StepStarted
from agent_core.llm import Usage
from mcp_erp.intent_diagnostics import IntentDiagnostics


def test_interrupted_segment_and_restored_execution_are_separate_observations(tmp_path):
    diagnostics = IntentDiagnostics("enforce")
    messages = [{"role": "user", "content": "请提交订单"}]
    path = tmp_path / "first.jsonl"
    first = diagnostics.begin("s", messages, path, kind="user_turn", model="script", run_id="run")
    first.observe(StepStarted(1))
    first.observe(StepEnd(1, None, 10))
    first.observe(ApprovalPending("call", "approval", "submit_order_draft", "high", {}))
    diagnostics.finish("s", first, error=asyncio.CancelledError())
    rows = [json.loads(r) for r in path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["data"]["unknown_usage_steps"] == [1]
    assert rows[-1]["data"]["outcome"] == "interrupted"
    assert rows[-1]["data"]["error_type"] == "CancelledError"
    assert diagnostics._active == {}
    second_path = tmp_path / "second.jsonl"
    second = diagnostics.begin("s", messages, second_path, kind="execution_continuation",
                               model="script", run_id="run")
    second.observe(StepStarted(2))
    second.observe(StepEnd(2, Usage(3, 2, 5), 10))
    second.observe(LoopEnd(2, Usage(3, 2, 5)))
    diagnostics.finish("s", second)
    resumed = [json.loads(r) for r in second_path.read_text(encoding="utf-8").splitlines()]
    assert resumed[0]["request_id"] != rows[0]["request_id"]
    assert resumed[0]["data"]["run_id"] == rows[0]["data"]["run_id"]
    assert resumed[0]["data"]["kind"] == "execution_continuation"
    assert resumed[-1]["data"]["unknown_usage_steps"] == []
    assert resumed[-1]["data"]["business_model_requests"] == 1


def test_crashed_prefix_is_readable_without_claiming_completion(tmp_path):
    diagnostics = IntentDiagnostics("shadow")
    path = tmp_path / "crash.jsonl"
    request = diagnostics.begin("s", [{"role": "user", "content": "你好"}], path,
                                kind="user_turn", model="script", run_id=None)
    request.observe(StepStarted(1))
    # Simulate only the already-flushed prefix surviving a process exit.
    rows = [json.loads(r) for r in path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["request_start", "business_model_start"]

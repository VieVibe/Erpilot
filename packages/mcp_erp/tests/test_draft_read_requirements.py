import json

import pytest
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import StreamEnd, TextDelta, ToolCall
from agent_core.runtime_config import LoopConfig
from erp_store.drafts import ErpDrafts
from evals import context_workflow as cw
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.bridge import build_agent_tools_async
from mcp_erp.draft_constraints import required_history_read, required_task_read


@pytest.mark.parametrize("question,expected", [
    ("只读查询草稿 " + "a" * 32 + " 的修改记录。", "a" * 32),
    ("请查看草稿 `" + "b" * 32 + "`的全部修改记录", "b" * 32),
    ("备注改为「查询草稿 " + "a" * 32 + " 的修改记录」", None),
    ("查询草稿 invalid 的修改记录", None),
])
def test_only_explicit_current_user_queries_select_read_target(question, expected):
    messages = [{"role": "user", "content": "查询草稿 " + "c" * 32 + " 的修改记录"},
                {"role": "user", "content": question}]
    selected = required_history_read(messages, [])
    assert (selected or {}).get("draft_id") == expected
    if selected:
        observed = [{"name": "get_order_draft_history", "arguments": selected, "ok": False,
                     "content": '{"error":{"code":"not_found"}}'}]
        assert required_history_read(messages, observed) is None  # Never replay failed reads.


def test_explicit_first_task_read_is_sequential_not_parallel_with_history():
    messages = [{"role": "user", "content": "只读查询草稿 " + "a" * 32
                 + " 的全部修改记录。先查询当前任务；历史工具每次limit=1。"}]
    assert required_task_read(messages, []) == {}
    assert required_history_read(messages, []) is None
    observed = [{"name": "get_order_task"}]
    assert required_task_read(messages, observed) is None
    assert required_history_read(messages, observed)["draft_id"] == "a" * 32


@pytest.mark.parametrize("foreign", [False, True])
async def test_actual_target_is_read_before_model_can_guess_available(tmp_path, foreign):
    engine = cw.seed_workflow(tmp_path / "erp.db", cw.Workflow("read-evidence", ()), "s1")
    try:
        draft = ErpDrafts(engine).prepare(
            "other" if foreign else "s1", "历史客户", [(cw.SKUS[0], 2)],
        )
        before = cw.snapshot_all(engine)
        tools = await build_agent_tools_async(tmp_path / "erp.db", session_id="s1")

        class Client:
            async def stream_chat(self, messages, tools=None):
                # Even a model that would have answered immediately receives actual evidence.
                payloads = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
                assert len(payloads) == 1
                available = payloads[0].get("draft_id") == draft["draft_id"]
                if foreign:
                    assert payloads[0]["error"]["code"] == "not_found"
                    assert "历史客户" not in json.dumps(messages, ensure_ascii=False)
                yield TextDelta(json.dumps({"available": available}))
                yield StreamEnd()

        runtime = LangGraphRuntime(Client(), tools, checkpointer=InMemorySaver())
        question = f"只读查询草稿 {draft['draft_id']} 的修改记录。"
        async for _ in runtime.stream([{"role": "user", "content": question}], thread_id="test"):
            pass
        state = (await runtime.get_state("test")).values
        assert json.loads(state["final_answer"]) == {"available": not foreign}
        assert state["tool_history"][0]["call_id"].startswith("application-read-")
        assert state["step"] == 1 and cw.snapshot_all(engine) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("fault", ["missing_sku", "wrong_sku", "no_read", "bool_revision"])
async def test_quantity_guard_rejects_before_any_revision_is_saved(tmp_path, fault):
    engine = cw.seed_workflow(tmp_path / "erp.db", cw.Workflow("quantity-guard", ()), "s1")
    try:
        drafts = ErpDrafts(engine)
        draft = drafts.prepare("s1", "保留客户", [(cw.SKUS[0], 2)])
        tools = {t.name: t for t in await build_agent_tools_async(
            tmp_path / "erp.db", session_id="s1",
        )}
        before = cw.snapshot_all(engine)
        args = {"draft_id": draft["draft_id"], "expected_revision": 1, "item_number": 1,
                "quantity": 5, "expected_sku": cw.SKUS[0]}
        history = [{"name": "get_order_task", "ok": True,
                    "content": json.dumps(drafts.task_state("s1"))}]
        if fault == "missing_sku":
            args.pop("expected_sku")
        elif fault == "wrong_sku":
            args["expected_sku"] = cw.SKUS[1]
        elif fault == "no_read":
            history = []
        else:
            args["expected_revision"] = True
        [call] = prepare_tool_calls(
            [ToolCall("guard", "revise_order_draft_quantity", json.dumps(args))], tools,
            messages=[{"role": "user", "content": "当前第一行改成5件，其他不变"}],
            tool_history=history,
        )
        result = await execute_tool_call(call, tools, LoopConfig())
        assert not result["ok"] and cw.snapshot_all(engine) == before
    finally:
        engine.dispose()

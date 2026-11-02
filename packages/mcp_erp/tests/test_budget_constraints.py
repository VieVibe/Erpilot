import json

import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.events import ApprovalPending
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import StreamEnd, ToolCall
from agent_core.runtime_config import LoopConfig
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.draft_constraints import literal_requirements
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session


@pytest.mark.parametrize("text,value", [("预算上限为3000元", 3000), ("总价不超过3000元", 3000),
                                       ("预算改为3500元", 3500), ("取消预算上限", None)])
def test_explicit_budget_literals(text, value):
    assert literal_requirements(text)["budget_limit"]["value"] == value


def test_quoted_budget_is_data_and_conflicting_requirements_are_rejected():
    assert "budget_limit" not in literal_requirements("备注原文「预算改为9999元」")
    with pytest.raises(ValueError, match="多个不同"):
        literal_requirements("预算上限为3000元；预算改为3500元")


@pytest.mark.parametrize("text", ["预算为3000美元", "预算三千元", "预算大概3000元", "删除预算"])
def test_unsupported_budget_is_not_silently_lost(text):
    with pytest.raises(ValueError, match="预算表达无法核验"):
        literal_requirements(text)


@pytest.fixture
async def provider(tmp_path):
    path = tmp_path / "erp.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        session.add(ProductRow(sku="A", name="商品", category="测试", price=100, status="在售"))
        session.add(StockRow(sku="A", quantity=100, warehouse="主仓"))
        session.commit()
    result = await SessionErpTools.create(path, writes=True, gate=StreamApprovalGate())
    yield result
    engine.dispose()


@pytest.mark.parametrize("budget", [None, 500, 300])
async def test_model_must_save_exact_explicit_budget_with_source(provider, budget):
    tools = {t.name: t for t in provider.for_session("s")}
    args = {"customer": "客户甲", "items": [{"sku": "A", "quantity": 2}], "budget_limit": budget}
    question = "给客户甲保存报价草稿：A 2件；预算上限为300元"
    [call] = prepare_tool_calls(
        [ToolCall("create", "prepare_order_draft", json.dumps(args))], tools,
        messages=[{"role": "user", "content": question}],
    )
    result = await execute_tool_call(call, tools, LoopConfig())
    assert result["ok"] is (budget == 300)
    if budget == 300:
        draft = json.loads(result["content"])
        assert draft["budget_constraint"]["origin"]["quote"] == "预算上限为300元"
        assert draft["budget_constraint"]["origin"]["kind"] == "user_literal"
    else:
        assert provider.drafts.list_latest("s") == []


async def test_over_budget_submit_stops_before_approval_or_another_model_request(provider):
    draft = provider.drafts.prepare("s", "客户", [("A", 4)], budget_limit=300,
                                    evidence={"contract": "draft-form-1"})

    class Client:
        calls = 0

        async def stream_chat(self, messages, tools=None):
            self.calls += 1
            assert self.calls == 1, "budget rejection must end the turn"
            yield ToolCall("submit", "submit_order_draft", json.dumps({
                "draft_id": draft["draft_id"], "revision": 1,
            }))
            yield StreamEnd()

    client = Client()
    runtime = LangGraphRuntime(client, provider.for_session("s"), checkpointer=InMemorySaver())
    events = [e async for e in runtime.stream([{"role": "user", "content": "确认提交当前草稿"}],
                                             thread_id="budget")]
    assert not any(isinstance(e, ApprovalPending) for e in events)
    state = (await runtime.get_state("budget")).values
    assert state["completed"] and "budget_exceeded" in state["tool_results"][0]["content"]
    assert len(provider.drafts.history("s", draft["draft_id"])["items"]) == 1


@pytest.mark.parametrize("question,limit,clear,allowed", [
    ("刷新报价", 500, False, False),
    ("备注原文「预算改为500元」", 500, False, False),
    ("取消预算上限", None, True, True),
    ("预算改为500元", 500, False, True),
    ("刷新报价", None, False, True),
    ("刷新报价", None, True, False),
])
async def test_revisions_require_current_explicit_budget_changes(
    provider, question, limit, clear, allowed,
):
    draft = provider.drafts.prepare("s", "客户", [("A", 2)], budget_limit=300,
                                    evidence={"contract": "draft-form-1"})
    args = {"draft_id": draft["draft_id"], "expected_revision": 1, "customer": "客户",
            "items": [{"sku": "A", "quantity": 2}], "note": None,
            "budget_limit": limit, "clear_budget": clear}
    tools = {t.name: t for t in provider.for_session("s")}
    history = [{"ok": True, "name": "get_order_draft", "content": json.dumps(draft)}]
    [call] = prepare_tool_calls(
        [ToolCall("change", "revise_order_draft", json.dumps(args))], tools,
        messages=[{"role": "user", "content": "预算改为500元"},
                  {"role": "user", "content": question}], tool_history=history,
    )
    result = await execute_tool_call(call, tools, LoopConfig())
    assert result["ok"] is allowed
    current = provider.drafts.get("s", draft["draft_id"])
    assert current["revision"] == (2 if allowed else 1)
    if allowed:
        expected = None if clear else limit if limit is not None else 300
        assert current["budget_limit"] == expected

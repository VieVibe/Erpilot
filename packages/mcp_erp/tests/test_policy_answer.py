import copy
import json
from datetime import UTC, datetime

import pytest
from agent_core.events import LoopEnd, TextDelta, ToolCallFinished
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import StreamEnd, ToolCall, Usage
from agent_core.runtime_config import LoopConfig
from erp_store.seed import seed_database
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp import build_agent_tools_async
from mcp_erp.policy_answer import POLICY_RESPONSE_CONTRACT, render_policy_answer
from mcp_erp.policy_tools import reference
from rag.corpus import load_corpus
from rag.index import Hit, PolicyIndex

QUESTION = "无理由退货期限是多少，收货地址和联系电话是什么？"


def evidence():
    chunk = next(c for c in load_corpus() if c.title == "七天无理由退货")
    ref = reference(Hit(chunk=chunk, score=1.0))
    payload = {
        "schema_version": 1, "query": "退货", "queried_at": datetime.now(UTC).isoformat(),
        "policy_state": "matched", "matches": [ref], "note": "命中不等于充分",
    }
    history = [{
        "name": "search_policy", "arguments": {"query": "退货"}, "ok": True,
        "content": json.dumps(payload, ensure_ascii=False),
    }]
    return history, payload, ref


def proposal(ref):
    return {"parts": [
        {"question_quote": "无理由退货期限是多少", "status": "evidence",
         "citations": [ref["citation"]]},
        {"question_quote": "收货地址和联系电话是什么", "status": "gap", "citations": []},
    ]}


def render(value, history=None, question=QUESTION):
    return render_policy_answer(
        value, evidence()[0] if history is None else history,
        [{"role": "user", "content": question}],
    )


def test_renderer_preserves_original_and_partial_gap():
    history, _, ref = evidence()
    result = render(proposal(ref), history)
    assert ref["quoted_citation"] in result["text"]
    assert "无法确认" in result["text"]
    assert not result["unassessed_remainder"]
    assert result["references"] == [ref]


def test_shared_passage_is_quoted_once_with_stable_program_references():
    _, _, ref = evidence()
    part = proposal(ref)["parts"][0]
    result = render({"parts": [part, part]})
    assert result["text"].count(ref["quoted_citation"]) == 1
    assert "参见证据 1 的原文" in result["text"]
    assert len(result["parts"]) == 2 and len(result["references"]) == 1


@pytest.mark.parametrize("mutation", ["citation", "text", "text_sha256", "quoted_citation"])
def test_corrupt_evidence_cannot_support_an_answer(mutation):
    history, payload, ref = evidence()
    payload["matches"][0][mutation] += "伪造"
    history[0]["content"] = json.dumps(payload)
    with pytest.raises(ValueError, match="absent"):
        render(proposal(ref), history)
    assert render(None, history)["references"] == []


@pytest.mark.parametrize("mutation", ["invented", "old_turn", "free_text", "gap_with_refs"])
def test_unbound_or_unstructured_proposals_are_rejected(mutation):
    history, _, ref = evidence()
    data = proposal(ref)
    if mutation == "invented":
        data["parts"][0]["citations"] = ["【made-up.md｜规则｜v1】"]
    elif mutation == "old_turn":
        history = []
    elif mutation == "free_text":
        data["parts"][0]["answer"] = "已查询您的订单，运费由店铺承担"
    else:
        data["parts"][0]["status"] = "gap"
    with pytest.raises(ValueError):
        render(data, history)


def test_question_must_come_from_current_user_and_remainder_is_explicit():
    _, _, ref = evidence()
    with pytest.raises(ValueError, match="current user"):
        render(proposal(ref), question="保修多少年？")
    one = {"parts": [proposal(ref)["parts"][0]]}
    result = render(one)
    assert result["unassessed_remainder"]
    assert "其余原话未列入本次证据选择" in result["text"]


@pytest.mark.parametrize("state", ["conflict", "inactive", "no_match", "unavailable"])
def test_blocked_results_only_render_gaps(state):
    history, payload, ref = evidence()
    payload.update(policy_state=state, matches=[])
    history[0]["content"] = json.dumps(payload)
    with pytest.raises(ValueError):
        render(proposal(ref), history)
    result = render(None, history)
    assert result["policy_state"] == state
    assert not result["references"]
    assert "7天" not in result["text"]


class Embedder:
    def embed(self, texts):
        return [[1.0] for _ in texts]


async def runtime(tmp_path, client, *, max_steps=5):
    db = tmp_path / "erp.db"
    seed_database(db, n_products=5, n_orders=2)
    embedder = Embedder()
    chunks = [c for c in load_corpus() if c.title == "七天无理由退货"]
    index = PolicyIndex.build(chunks, embedder)
    tools = await build_agent_tools_async(db, policy_resolver=lambda: (index, embedder))
    assert {t.response_contract.key for t in tools if t.response_contract} == {
        POLICY_RESPONSE_CONTRACT.key
    }
    schema = next(t for t in tools if t.name == "compose_policy_answer").openai_schema()
    parameters = schema["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["$defs"]["PolicyAnswerPart"]["additionalProperties"] is False
    return LangGraphRuntime(
        client, tools, checkpointer=InMemorySaver(), config=LoopConfig(max_steps=max_steps),
    )


class Script:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = 0

    async def stream_chat(self, messages, tools=None):
        self.calls += 1
        for event in next(self.steps):
            yield event
        yield StreamEnd(Usage(13, 7, 20))


def call(name, args, cid="c1"):
    return ToolCall(cid, name, json.dumps(args, ensure_ascii=False))


async def test_runtime_suppresses_drafts_and_stops_after_program_response(tmp_path):
    ref = evidence()[2]
    client = Script([
        [TextDelta("历史订单已经查询过，运费商家承担"), call("search_policy", {"query": "退货"})],
        [TextDelta("Federal风险期限"), call("compose_policy_answer", proposal(ref), "c2")],
    ])
    agent = await runtime(tmp_path, client)
    messages = [{"role": "user", "content": QUESTION}]
    events = [e async for e in agent.run(messages, thread_id="p")]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "Federal" not in text and "已经查询" not in text
    assert ref["quoted_citation"] in text and client.calls == 2
    assert isinstance(events[-1], LoopEnd) and events[-1].completed
    assert messages[-1] == {"role": "assistant", "content": text}
    state = (await agent.get_state("p")).values
    assert len(state["suppressed_drafts"]) == 2
    assert "Federal" not in json.dumps(state["messages"])
    # The next turn cannot use the previous turn's evidence.
    client.steps = iter([[call("compose_policy_answer", proposal(ref))], [TextDelta("假回答")]])
    events = [e async for e in agent.run(
        [*messages, {"role": "user", "content": QUESTION}], thread_id="p"
    )]
    assert any(isinstance(e, ToolCallFinished) and not e.ok for e in events)
    assert ref["quoted_citation"] not in "".join(
        e.text for e in events if isinstance(e, TextDelta)
    )


async def test_parallel_finalizer_cannot_bind_unseen_evidence(tmp_path):
    client = Script([[call("search_policy", {"query": "退货"}),
                      call("compose_policy_answer", proposal(evidence()[2]), "c2")]])
    agent = await runtime(tmp_path, client, max_steps=1)
    events = [e async for e in agent.stream(
        [{"role": "user", "content": QUESTION}], thread_id="parallel"
    )]
    result = next(e for e in events if isinstance(e, ToolCallFinished)
                  and e.name == "compose_policy_answer")
    assert not result.ok and "separate tool step" in result.content
    assert events[-1].completed


async def test_free_text_after_search_becomes_explicit_fallback(tmp_path):
    client = Script([[call("search_policy", {"query": "退货"})], [TextDelta("运费全由商家承担")]])
    agent = await runtime(tmp_path, client)
    events = [e async for e in agent.stream(
        [{"role": "user", "content": QUESTION}], thread_id="fallback"
    )]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "运费全由商家承担" not in text
    assert "尚未完成逐问适用性判断" in text and "无法确认" in text


def test_renderer_does_not_mutate_tool_history():
    history, _, ref = evidence()
    original = copy.deepcopy(history)
    render(proposal(ref), history)
    assert history == original

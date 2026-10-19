import json
import sqlite3
from pathlib import Path

import httpx2
import pytest
from agent_core.approval import StreamApprovalGate
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.seed import seed_database
from erpilot_api.main import create_app
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.bridge import build_agent_tools_async
from mcp_erp.policy_tools import reference
from rag.corpus import load_corpus
from rag.index import Hit, PolicyIndex


def test_browser_evidence_fixture_uses_exact_corpus_text_and_metadata():
    path = Path(__file__).resolve().parents[2] / "web/tests/policy-search-fixture.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    chunks = {(chunk.source, chunk.title): chunk for chunk in load_corpus()}
    for citation in fixture["matches"]:
        chunk = chunks[citation["source"], citation["title"]]
        assert citation == reference(Hit(chunk=chunk, score=citation["score"]))


@pytest.mark.parametrize(
    "query,state", [("退货", "matched"), ("火星天气", "no_match"), ("不可用", "failed")]
)
async def test_policy_evidence_survives_sse_and_restore_without_retrieval_or_writes(
    tmp_path, query, state
):
    db = tmp_path / "erp.db"
    seed_database(db, n_products=3, n_orders=2)

    class Embedder:
        def embed(self, texts):
            return [[float(text.count("退货")), float(text.count("折扣"))] for text in texts]

    embedder = Embedder()
    index = PolicyIndex.build(load_corpus(), embedder)
    resolutions = []

    def resolver():
        resolutions.append(query)
        if query == "不可用" or len(resolutions) > 1:
            raise RuntimeError("secret-key-sensitive")
        return index, embedder

    gate = StreamApprovalGate()
    tools = await build_agent_tools_async(
        db, writes=True, approval_gate=gate, session_id="rag", policy_resolver=resolver
    )

    def model(request):
        messages = json.loads(request.content)["messages"]
        if messages[-1]["role"] == "tool":
            return sse_response(
                [chunk(delta={"content": "已展示本次检索证据。"}), chunk(usage=USAGE)]
            )
        return sse_response(
            tool_call_chunks("rag-search", "search_policy", json.dumps({"query": query}))
        )

    options = dict(
        client_factory=lambda: make_client(model),
        tools=tools,
        trace_dir=tmp_path / "traces",
        checkpointer=InMemorySaver(),
        approval_gate=gate,
    )
    with sqlite3.connect(db) as connection:
        before = "\n".join(connection.iterdump())
    app = create_app(**options)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/chat/stream", json={"session_id": "rag", "message": query}
        )
        assert response.status_code == 200 and "event: done" in response.text
        assert "event: approval_pending" not in response.text
        snapshot = (await client.get("/api/sessions/rag/state")).json()
    display = snapshot["tool_results"][0]["display"]
    assert display["kind"] == "policy_search" and display["query"] == query
    assert display["outcome"] == ("failed" if state == "failed" else "succeeded")
    if state != "failed":
        assert display["search"]["policy_state"] == state
        assert bool(display["search"]["matches"]) == (state == "matched")
    assert "secret-key" not in response.text
    streamed = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert any(item.get("display") == display for item in streamed)
    restored = create_app(**options)
    recovered = await restored.state.service.session_state("rag")
    assert recovered["tool_results"][0]["display"] == display
    assert recovered["pending_approvals"] == [] and resolutions == [query]
    with sqlite3.connect(db) as connection:
        assert "\n".join(connection.iterdump()) == before


async def test_program_answer_is_identical_in_sse_history_and_restored_session(tmp_path):
    db = tmp_path / "erp.db"
    seed_database(db, n_products=3, n_orders=2)
    question = "无理由退货有几天期限？"

    class Embedder:
        def embed(self, texts):
            return [[1.0] for _ in texts]

    embedder = Embedder()
    chunks = [c for c in load_corpus() if c.title == "七天无理由退货"]
    index = PolicyIndex.build(chunks, embedder)
    tools = await build_agent_tools_async(db, policy_resolver=lambda: (index, embedder))
    requests = []

    def model(request):
        messages = json.loads(request.content)["messages"]
        requests.append(messages)
        if messages[-1]["role"] == "tool":
            refs = json.loads(messages[-1]["content"])["matches"]
            parts = [{"question_quote": question, "status": "evidence",
                      "citations": [refs[0]["citation"]]}]
            return sse_response([
                chunk(delta={"content": "这笔订单已经核对过，符合退货要求。"}),
                *tool_call_chunks("answer", "compose_policy_answer", json.dumps({"parts": parts})),
            ])
        return sse_response(tool_call_chunks("search", "search_policy",
                                            json.dumps({"query": question})))

    options = dict(client_factory=lambda: make_client(model), tools=tools,
                   trace_dir=tmp_path / "traces", checkpointer=InMemorySaver())
    app = create_app(**options)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/api/chat/stream",
                                     json={"session_id": "structured", "message": question})
        state = (await client.get("/api/sessions/structured/state")).json()
    assert len(requests) == 2 and "event: done" in response.text
    assert "这笔订单已经核对过" not in response.text
    final = state["messages"][-1]["content"]
    assert chunks[0].text in final
    assert state["tool_results"][-1]["name"] == "compose_policy_answer"
    restored = create_app(**options)
    snapshot = await restored.state.service.session_state("structured")
    assert snapshot["messages"][-1]["content"] == final
    deltas = [json.loads(block.split("data: ", 1)[1])["text"]
              for block in response.text.replace("\r\n", "\n").split("\n\n")
              if block.startswith("event: delta\n")]
    assert "".join(deltas) == final

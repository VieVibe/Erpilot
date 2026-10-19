import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_core.llm import StreamEnd, ToolCall, Usage
from evals.billing import EndpointRate
from evals.campaign import _lock, request_totals, run_campaign
from evals.grounding_cases import STRUCTURED_GROUNDING_CASES
from evals.grounding_runtime import build_grounding_harness

RATE = EndpointRate("test-model", "test.local", "CNY", Decimal("1"), Decimal("2"))


class Client:
    config = SimpleNamespace(model="test-model", base_url="https://test.local/v1")
    request_timeout = 30

    def __init__(self, *, interrupt_at=None, gaps=()):
        self.requests = 0
        self.interrupt_at = interrupt_at
        self.gaps = gaps

    async def stream_chat(self, messages, tools=None):
        self.requests += 1
        if self.requests == self.interrupt_at:
            raise TimeoutError("upstream response interrupted")
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if messages[-1]["role"] == "tool":
            payload = json.loads(messages[-1]["content"])
            gap = question in self.gaps
            args = {"parts": [{
                "question_quote": question, "status": "gap" if gap else "evidence",
                "citations": [] if gap else [ref["citation"] for ref in payload["matches"]],
            }]}
            yield ToolCall("final", "compose_policy_answer", json.dumps(args, ensure_ascii=False))
        else:
            yield ToolCall("search", "search_policy", json.dumps({"query": question}))
        yield StreamEnd(Usage(13, 7, 20))


async def run(tmp_path, client, **kwargs):
    return await run_campaign(
        STRUCTURED_GROUNDING_CASES[:3], client=client, rate=RATE, limit=Decimal("1"),
        manifest_path=tmp_path / "campaign.json", case_harness=build_grounding_harness(),
        report_dir=tmp_path / "reports", trace_dir=tmp_path / "traces", **kwargs,
    )


async def test_interrupt_freezes_segment_and_explicit_resume_only_finishes_pending(tmp_path):
    first = Client(interrupt_at=3)
    summary, path = await run(tmp_path, first)
    assert first.requests == 3
    assert summary["finished"] == summary["passed"] == 1
    assert summary["unknown_requests"] == 1 and not summary["acceptance_passed"]
    before = json.loads(path.read_text(encoding="utf-8"))["segments"][0]
    frozen_report = Path(before["report"]).read_bytes()
    frozen_journal = Path(before["journal"]).read_bytes()
    second = Client()
    with pytest.raises(ValueError, match="resume-with-unknown"):
        await run(tmp_path, second, resume=True)
    assert second.requests == 0
    summary, _ = await run(tmp_path, second, resume=True, allow_unknown=True)
    assert second.requests == 4  # First completed case is never rerun.
    assert summary["finished"] == summary["passed"] == 3 and summary["all_cases_passed"]
    assert summary["requests"] == 7 and summary["unknown_requests"] == 1
    assert not summary["acceptance_passed"] and not summary["billing_complete"]
    from evals.compare import _complete_scope

    aggregate = json.loads(path.with_name("campaign-summary.json").read_text(encoding="utf-8"))
    assert aggregate["suite"]["billing_budget"]["unknown_usage"]
    assert not _complete_scope(aggregate)
    assert {a["status"] for a in summary["attempts"]} >= {"passed", "upstream_interrupted"}
    assert Path(before["report"]).read_bytes() == frozen_report
    assert Path(before["journal"]).read_bytes() == frozen_journal
    assert json.loads(path.read_text(encoding="utf-8"))["segments"][0] == before


async def test_completed_model_failure_is_preserved_instead_of_retried(tmp_path):
    first = Client(gaps=[STRUCTURED_GROUNDING_CASES[0].question])
    summary, _ = await run(tmp_path, first, case_limit=1)
    assert summary["finished"] == 1 and summary["passed"] == 0
    assert summary["attempts"][0]["status"] == "model_failed"
    second = Client()
    summary, _ = await run(tmp_path, second, resume=True)
    assert second.requests == 4 and summary["complete_scope"]
    assert summary["passed"] == 2 and not summary["acceptance_passed"]


async def test_clean_segments_can_complete_and_finished_resume_is_noop(tmp_path):
    summary, path = await run(tmp_path, Client(), case_limit=1)
    assert not summary["complete_scope"]
    summary, _ = await run(tmp_path, Client(), resume=True)
    assert summary["acceptance_passed"] and summary["requests"] == 6
    from evals.compare import _complete_scope

    aggregate = json.loads(path.with_name("campaign-summary.json").read_text(encoding="utf-8"))
    assert _complete_scope(aggregate)
    before = path.read_bytes()
    extra = Client()
    summary, _ = await run(tmp_path, extra, resume=True)
    assert extra.requests == 0 and summary["acceptance_passed"]
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change", ["timeout", "model", "thinking", "rate", "report", "journal", "source"],
)
async def test_resume_rejects_changed_identity_or_evidence(tmp_path, monkeypatch, change):
    _, path = await run(tmp_path, Client(), case_limit=1)
    client = Client()
    if change == "timeout":
        client.request_timeout = 31
    elif change == "model":
        client.config = SimpleNamespace(model="other-model", base_url=client.config.base_url)
    elif change == "thinking":
        client.config = SimpleNamespace(model=client.config.model, base_url=client.config.base_url,
                                       thinking=True)
    elif change == "rate":
        monkeypatch.setattr(__name__ + ".RATE", replace(RATE, input_per_m=Decimal("3")))
    elif change == "source":
        monkeypatch.setattr(
            "evals.campaign.source_provenance", lambda: {"source_sha256": "changed"},
        )
    else:
        segment = json.loads(path.read_text(encoding="utf-8"))["segments"][0]
        with open(segment[change], "a", encoding="utf-8") as stream:
            stream.write(" ")
    with pytest.raises(ValueError, match="changed"):
        await run(tmp_path, client, resume=True)
    assert client.requests == 0


async def test_concurrent_campaign_launch_cannot_issue_requests(tmp_path):
    client = Client()
    with (_lock(tmp_path / "campaign.json.lock"),
          pytest.raises(ValueError, match="already running")):
        await run(tmp_path, client)
    assert client.requests == 0


def test_started_request_without_settlement_is_unknown_even_after_process_crash(tmp_path):
    journal = tmp_path / "requests.jsonl"
    journal.write_text(json.dumps({"request": 1, "status": "started"}) + "\n", encoding="utf-8")
    assert request_totals(journal) == {"requests": 1, "known_spent": "0", "unknown_requests": 1}


async def test_sdk_hidden_retries_rejected_before_any_request(tmp_path):
    client = Client()
    client._client = SimpleNamespace(max_retries=2)
    with pytest.raises(ValueError, match="max_retries=0"):
        await run(tmp_path, client)
    assert client.requests == 0

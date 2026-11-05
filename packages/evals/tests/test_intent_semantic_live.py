from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_core.billing import EndpointRate
from agent_core.llm import LLMConfig, StreamEnd, TextDelta
from evals import intent_semantic_live as probe


@pytest.mark.parametrize("revision", [False, True])
async def test_unknown_usage_stops_paid_cases_but_checks_no_request_guards(
    tmp_path, monkeypatch, revision,
):
    config = LLMConfig("fixture-key", model="fixture", base_url="https://fixture.invalid/v1")
    rate = EndpointRate("fixture", "fixture.invalid", "CNY", Decimal(1), Decimal(2),
                        price_source="fixture")
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.config = config
            self._client = SimpleNamespace(close=AsyncMock())

        def without_retries(self):
            return self

        async def stream_chat(self, messages, tools=None):
            calls.append(messages)
            yield TextDelta("{}")
            yield StreamEnd(None)

    monkeypatch.setattr(probe, "load_dotenv", lambda: None)
    monkeypatch.setattr(probe, "LLMClient", Client)
    monkeypatch.setattr(probe.LLMConfig, "from_env", lambda: config)
    monkeypatch.setattr(probe, "endpoint_rate", lambda *args: rate)
    fixtures = probe.REVISION_CASES if revision else probe.CASES
    indices = [0, 2, 3] if revision else [0, 4, 5]
    monkeypatch.setattr(probe, "REVISION_CASES" if revision else "CASES",
                        [fixtures[i] for i in indices])
    output = tmp_path / "first"
    result = await probe.run(output, limit=Decimal("0.05"), max_requests=5, timeout=1,
                             revision=revision)
    assert len(calls) == result["provider_requests"] == 1
    assert result["unknown_usage"] and not result["passed"]
    assert result["cases"][0]["status"] == "failed"
    assert result["cases"][1]["status"] == "not_run_budget"
    assert result["cases"][2]["status"] == "passed"
    assert result["business_unchanged"] and result["source_unchanged"]
    assert (output / "request-001.json").is_file()
    assert (output / "report.json").is_file()


async def test_evidence_directory_is_single_use(tmp_path):
    import pytest

    with pytest.raises(FileExistsError):
        await probe.run(tmp_path, limit=Decimal("0.05"), max_requests=5, timeout=1)

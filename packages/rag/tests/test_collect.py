import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from rag.benchmark import load_dataset, prepare_inputs
from rag.collect import collect_queries
from rag.corpus import load_corpus
from rag.index import PolicyIndex


@pytest.fixture
def inputs(tmp_path):
    dataset = load_dataset()
    index_path = tmp_path / "index.json"
    PolicyIndex(load_corpus(), [[1.0, 0.0]] * 18).save(index_path)
    return dataset, index_path, tmp_path / "vectors.json", tmp_path / "audit.json"


async def collect(inputs, handler, **changes):
    dataset, index_path, output, audit = inputs
    client = SimpleNamespace(embeddings=SimpleNamespace(create=handler))
    return await collect_queries(
        dataset, client=client, model="fixture", endpoint_host="test.local",
        index_path=index_path, output=output, audit_path=audit, **changes,
    )


def response(texts, *, tokens=10):
    return SimpleNamespace(
        model="fixture", usage=SimpleNamespace(total_tokens=tokens, prompt_tokens=tokens),
        data=[SimpleNamespace(index=i, embedding=[1.0, float(i)])
              for i in reversed(range(len(texts)))],
    )


async def test_complete_collection_binds_cache_and_writes_attempt_before_http(inputs):
    calls = []

    async def handler(*, model, input):  # noqa: A002
        ledger = json.loads(inputs[3].read_text(encoding="utf-8"))
        assert ledger["requests"][-1]["status"] == "started"
        assert len(ledger["requests"]) == len(calls) + 1
        calls.append(input)
        return response(input)

    audit = await collect(inputs, handler)
    assert audit["complete"] and not audit["unknown_usage"]
    assert [len(batch) for batch in calls] == [10, 10, 10, 6]
    assert audit["known_total_tokens"] == 40 and audit["estimated_cost"] is None
    assert audit["output_sha256"] == hashlib.sha256(inputs[2].read_bytes()).hexdigest()
    _, vectors, _ = prepare_inputs(inputs[0], load_corpus(), backend="cached",
                                   index_path=inputs[1], query_vectors_path=inputs[2])
    assert vectors[inputs[0].cases[1].query] == [1.0, 1.0]
    with pytest.raises(ValueError, match="never reset"):
        await collect(inputs, handler)
    assert len(calls) == 4


@pytest.mark.parametrize("fault", ["missing_usage", "missing_total", "zero_usage", "transport",
                                   "timeout", "duplicate_index", "bad_vector", "wrong_model"])
async def test_unknown_or_invalid_response_stops_before_another_request(inputs, fault):
    calls = 0
    started = asyncio.Event()

    async def handler(*, model, input):  # noqa: A002
        nonlocal calls
        calls += 1
        if fault == "transport":
            raise ConnectionError("test interruption")
        if fault == "timeout":
            await started.wait()
        result = response(input)
        if fault == "missing_usage":
            result.usage = None
        elif fault == "missing_total":
            result.usage.total_tokens = None
        elif fault == "zero_usage":
            result.usage.total_tokens = 0
        elif fault == "duplicate_index":
            result.data[0].index = result.data[1].index
        elif fault == "bad_vector":
            result.data[0].embedding = [float("nan"), 1.0]
        elif fault == "wrong_model":
            result.model = "different"
        return result

    if fault in {"transport", "timeout"}:
        with pytest.raises((ConnectionError, TimeoutError)):
            await collect(inputs, handler, timeout=0.01)
    else:
        assert not (await collect(inputs, handler))["complete"]
    audit = json.loads(inputs[3].read_text(encoding="utf-8"))
    assert calls == 1 and len(audit["requests"]) == 1
    assert audit["unknown_usage"] is (fault in {"transport", "timeout", "missing_usage",
                                               "missing_total", "zero_usage"})
    assert not inputs[2].exists()


async def test_token_limit_keeps_known_vectors_and_stops_collection(inputs):
    calls = 0

    async def handler(*, model, input):  # noqa: A002
        nonlocal calls
        calls += 1
        return response(input, tokens=15)

    audit = await collect(inputs, handler, max_total_tokens=15)
    assert calls == 1 and audit["status"] == "token_limit" and not audit["complete"]
    assert len(audit["query_vectors"]) == 10 and audit["known_total_tokens"] == 15


@pytest.mark.parametrize("changes", [{"max_requests": 3}, {"max_requests": True},
                                     {"max_total_tokens": 0}, {"timeout": float("nan")}])
async def test_invalid_limits_make_no_request_or_audit(inputs, changes):
    async def forbidden(**kwargs):
        raise AssertionError("no HTTP before validation")

    with pytest.raises(ValueError):
        await collect(inputs, forbidden, **changes)
    assert not inputs[3].exists()

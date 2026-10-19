"""Bounded live query embedding collection, separate from offline benchmarks.

Persist each attempted request before sending it. Never retry or reopen a ledger;
unknown usage stops collection and cannot silently become a fresh budget.
"""

import argparse
import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from rag.benchmark import (
    DEFAULT_DATASET,
    VectorBundle,
    corpus_fingerprint,
    fingerprint,
    load_dataset,
)
from rag.corpus import DEFAULT_INDEX_PATH, load_corpus
from rag.index import PolicyIndex
from rag.openai_embedder import EmbedConfig


def _now():
    return datetime.now(UTC).isoformat()


def _write_audit(path, payload):
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def collect_queries(
    dataset, *, client, model, endpoint_host, index_path, output, audit_path,
    max_requests=4, max_total_tokens=3000, timeout=30.0, batch_size=10,
):
    for value in (max_requests, max_total_tokens, batch_size):
        if type(value) is not int or value <= 0:
            raise ValueError("request, token and batch limits must be positive integers")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if output.exists() or audit_path.exists():
        raise ValueError("output/audit already exists; never reset or resume a paid ledger")
    if len({output.resolve(), audit_path.resolve(), index_path.resolve()}) != 3:
        raise ValueError("output and audit must not overwrite the input index")
    index = PolicyIndex.load(index_path)
    chunks = load_corpus()
    if index.chunks != chunks or corpus_fingerprint(chunks) != dataset.corpus_sha256:
        raise ValueError("index/corpus does not match frozen dataset")
    dimensions = len(index.vectors[0])
    if not dimensions or any(
        len(vector) != dimensions or not any(vector)
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in vector) for vector in index.vectors
    ):
        raise ValueError("invalid stored document vectors")
    cases = dataset.cases
    if math.ceil(len(cases) / batch_size) > max_requests:
        raise ValueError("request cap cannot cover the complete frozen query set")
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit = {"kind": "live-query-collection-1", "started_at": _now(), "complete": False,
             "status": "started", "model": model, "endpoint_host": endpoint_host,
             "dataset_sha256": fingerprint(dataset.model_dump()),
             "corpus_sha256": dataset.corpus_sha256,
             "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
             "dimensions": dimensions, "planned_queries": len(cases),
             "input_characters": sum(len(case.query) for case in cases),
             "limits": {"max_requests": max_requests, "max_total_tokens": max_total_tokens,
                        "batch_size": batch_size, "timeout_seconds": timeout, "sdk_retries": 0},
             "requests": [], "known_total_tokens": 0, "unknown_usage": False,
             "estimated_cost": None, "cost_note": "embedding endpoint price not confirmed",
             "query_vectors": {}}
    # Exclusive create prevents an invocation from replacing an earlier paid run.
    with audit_path.open("x", encoding="utf-8") as stream:
        json.dump(audit, stream, ensure_ascii=False, indent=2)
    try:
        for start in range(0, len(cases), batch_size):
            if audit["known_total_tokens"] >= max_total_tokens:
                audit["status"] = "token_limit"
                break
            batch = cases[start:start + batch_size]
            record = {"number": len(audit["requests"]) + 1,
                      "case_ids": [case.id for case in batch], "status": "started",
                      "started_at": _now(), "usage": None}
            audit["requests"].append(record)
            _write_audit(audit_path, audit)  # durably count the attempt before HTTP
            try:
                response = await asyncio.wait_for(client.embeddings.create(
                    model=model, input=[case.query for case in batch],
                ), timeout=timeout)
            except BaseException as exc:
                record.update(status="unknown_usage", error_type=type(exc).__name__,
                              ended_at=_now())
                audit.update(status="unknown_usage", unknown_usage=True)
                raise
            usage = getattr(response, "usage", None)
            total = getattr(usage, "total_tokens", None)
            record.update(response_id=getattr(response, "id", None), ended_at=_now())
            if type(total) is not int or total <= 0:
                record["status"] = "unknown_usage"
                audit.update(status="unknown_usage", unknown_usage=True)
                break
            record["usage"] = {"total_tokens": total,
                               "prompt_tokens": getattr(usage, "prompt_tokens", None)}
            audit["known_total_tokens"] += total
            actual_model = getattr(response, "model", None)
            record["response_model"] = actual_model
            if actual_model and actual_model != model:
                record["status"] = "invalid_response_model"
                audit["status"] = "invalid_response"
                break
            items = response.data
            if (len(items) != len(batch)
                    or any(type(item.index) is not int for item in items)
                    or {item.index for item in items} != set(range(len(batch)))):
                record["status"] = "invalid_response_indices"
                audit["status"] = "invalid_response"
                break
            vectors = [list(item.embedding) for item in sorted(items, key=lambda item: item.index)]
            if any(len(v) != dimensions or not any(v)
                   or any(isinstance(n, bool) or not isinstance(n, (int, float))
                          or not math.isfinite(n) for n in v) for v in vectors):
                record["status"] = "invalid_response_vectors"
                audit["status"] = "invalid_response"
                break
            audit["query_vectors"].update(dict(zip(
                [case.query for case in batch], vectors, strict=True,
            )))
            record["status"] = "complete"
            _write_audit(audit_path, audit)
        if len(audit["query_vectors"]) == len(cases) and not audit["unknown_usage"]:
            bundle = VectorBundle(
                schema_version=1, model=model,
                provenance=(f"live collection; audit={audit_path.as_posix()}; "
                            f"started={audit['started_at']}"),
                index_sha256=audit["index_sha256"], corpus_sha256=dataset.corpus_sha256,
                dimensions=dimensions, query_vectors=audit["query_vectors"],
            )
            with output.open("x", encoding="utf-8") as stream:
                stream.write(bundle.model_dump_json(indent=2) + "\n")
            audit.update(complete=True, status="complete",
                         output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    finally:
        if audit["status"] == "started":
            audit["status"] = "interrupted"
        audit["token_limit_exceeded"] = audit["known_total_tokens"] > max_total_tokens
        audit["ended_at"] = _now()
        _write_audit(audit_path, audit)
    return audit


def main():
    parser = argparse.ArgumentParser(description="Explicit bounded real query embedding collection")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--max-requests", type=int, default=4)
    parser.add_argument("--max-total-tokens", type=int, default=3000)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    config = EmbedConfig.from_env()
    if config.model != args.expected_model:
        parser.error("configured model differs from --expected-model")
    if any(path.resolve() == args.dataset.resolve() for path in (args.output, args.audit)):
        parser.error("output/audit must not overwrite frozen dataset")
    from openai import AsyncOpenAI

    async def run():
        async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                               max_retries=0, timeout=args.timeout) as client:
            return await collect_queries(
                load_dataset(args.dataset), client=client, model=config.model,
                endpoint_host=urlsplit(config.base_url).hostname,
                index_path=args.index, output=args.output, audit_path=args.audit,
                max_requests=args.max_requests, max_total_tokens=args.max_total_tokens,
                timeout=args.timeout,
            )

    try:
        audit = asyncio.run(run())
    except Exception as exc:
        parser.exit(1, f"Collection stopped: {type(exc).__name__}; inspect audit; no retry\n")
    print(json.dumps({key: audit[key] for key in (
        "complete", "status", "known_total_tokens", "unknown_usage", "estimated_cost",
    )}))
    raise SystemExit(0 if audit["complete"] else 1)


if __name__ == "__main__":
    main()

"""Offline retrieval experiments with explicit proxy or complete cached vectors.

No .env loading, network clients or implicit embedding fallback. Only development
labels select cutoffs; held-out cases are scored after cutoffs are frozen.
"""

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.chunk import Chunk
from rag.corpus import DEFAULT_INDEX_PATH, load_corpus
from rag.evidence import select_policy_scope
from rag.index import PolicyIndex
from rag.lexical import (
    RANKER_VERSION,
    TOKENIZER_VERSION,
    BM25Index,
    RankedHit,
    chunk_id,
    reciprocal_rank_fusion,
    tokenize,
)

DEFAULT_DATASET = Path(__file__).parent / "benchmarks/policy-retrieval-v1.json"
SCORER_VERSION = "retrieval-evidence-1"
PROXY_MODEL = "body-bigram-tfidf-proxy-1"


def fingerprint(payload) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def corpus_fingerprint(chunks: list[Chunk]) -> str:
    return fingerprint([chunk.model_dump(mode="json") for chunk in chunks])


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class EvidenceRef(_Strict):
    source: str = Field(min_length=1)
    title: str = Field(min_length=1)


class RetrievalCase(_Strict):
    id: str = Field(min_length=1)
    split: Literal["dev", "test"]
    query: str = Field(min_length=1)
    group: str = Field(min_length=1)
    relevant: list[EvidenceRef]
    rationale: str = Field(min_length=1)


class RetrievalDataset(_Strict):
    version: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    at: str
    k: int = Field(ge=1, le=5)
    rrf_constant: int = Field(gt=0)
    cases: list[RetrievalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        at = datetime.fromisoformat(self.at)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("benchmark time must include timezone")
        for key in ("id", "query"):
            values = [getattr(case, key).strip() for case in self.cases]
            if any(not value for value in values) or len(values) != len(set(values)):
                raise ValueError(f"case {key} must be unique and non-empty")
        for case in self.cases:
            refs = [(ref.source, ref.title) for ref in case.relevant]
            if len(refs) != len(set(refs)):
                raise ValueError("duplicate relevance judgment")
        for split in ("dev", "test"):
            kinds = {bool(case.relevant) for case in self.cases if case.split == split}
            if kinds != {True, False}:
                raise ValueError("each split needs answerable and out-of-corpus cases")
        return self


def load_dataset(path: Path = DEFAULT_DATASET) -> RetrievalDataset:
    return RetrievalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


class _TfidfProxy:
    """Lexical cosine proxy fitted only on corpus bodies; no semantic model claim."""

    def __init__(self, chunks: list[Chunk]):
        docs = [Counter(tokenize(chunk.text)) for chunk in chunks]
        df = Counter(term for counts in docs for term in counts)
        self.vocabulary = sorted(df)
        self.idf = {term: math.log((len(docs) + 1) / (df[term] + 1)) + 1 for term in df}
        if not self.vocabulary:
            raise ValueError("proxy requires a non-empty corpus vocabulary")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            counts = Counter(tokenize(text))
            vectors.append([(1 + math.log(counts[term])) * self.idf[term] if counts[term] else 0.0
                            for term in self.vocabulary])
        return vectors


class VectorBundle(_Strict):
    schema_version: int
    model: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: int = Field(gt=0)
    query_vectors: dict[str, list[float]]

    @model_validator(mode="after")
    def validate_vectors(self) -> Self:
        if self.schema_version != 1:
            raise ValueError("unsupported vector bundle schema")
        for vector in self.query_vectors.values():
            if len(vector) != self.dimensions or not any(vector):
                raise ValueError("query vectors must be nonzero and match dimensions")
        return self


class _FixedVector:
    def __init__(self, query: str, vector: list[float]):
        self.query, self.vector = query, vector

    def embed(self, texts):
        if texts != [self.query]:
            raise ValueError("unexpected query; offline benchmark has no embedding fallback")
        return [self.vector]


def prepare_inputs(dataset, chunks, *, backend, index_path, query_vectors_path=None):
    if corpus_fingerprint(chunks) != dataset.corpus_sha256:
        raise ValueError("frozen corpus fingerprint changed; create a new dataset version")
    existing = {(chunk.source, chunk.title) for chunk in chunks}
    if any((ref.source, ref.title) not in existing
           for case in dataset.cases for ref in case.relevant):
        raise ValueError("relevance judgment points to a missing section")
    if backend == "local-tfidf":
        if query_vectors_path is not None:
            raise ValueError("local proxy must not consume a real query-vector bundle")
        proxy = _TfidfProxy(chunks)
        index = PolicyIndex.build(chunks, proxy)
        queries = [case.query for case in dataset.cases]
        vectors = dict(zip(queries, proxy.embed(queries), strict=True))
        return index, vectors, {"kind": "lexical-proxy", "model": PROXY_MODEL,
                                "is_production_embedding": False}
    if backend != "cached" or query_vectors_path is None:
        raise ValueError("cached backend requires --query-vectors; no network fallback")
    bundle = VectorBundle.model_validate(json.loads(query_vectors_path.read_text(encoding="utf-8")))
    if bundle.index_sha256 != hashlib.sha256(index_path.read_bytes()).hexdigest():
        raise ValueError("query bundle index fingerprint mismatch")
    if bundle.corpus_sha256 != dataset.corpus_sha256:
        raise ValueError("query bundle corpus fingerprint mismatch")
    required = {case.query for case in dataset.cases}
    if set(bundle.query_vectors) != required:
        raise ValueError("query bundle must contain exactly every frozen query; no partial runs")
    index = PolicyIndex.load(index_path)
    if index.chunks != chunks:
        raise ValueError("stored index does not match frozen corpus")
    if any(len(vector) != bundle.dimensions or not any(vector)
           or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                  for v in vector) for vector in index.vectors):
        raise ValueError("stored vectors are invalid or have incompatible dimensions")
    return index, bundle.query_vectors, {
        "kind": "cached-vectors", "model": bundle.model, "provenance": bundle.provenance,
        "is_production_embedding": None,
        "model_identity": "declared; bound to index bytes, not independently verified",
        "index_sha256": bundle.index_sha256,
        "query_bundle_sha256": hashlib.sha256(query_vectors_path.read_bytes()).hexdigest(),
    }


def rank_query(index, lexical, query, vector, *, rrf_constant):
    started = time.perf_counter()
    hits = index.search(query, _FixedVector(query, vector), k=len(index.chunks), threshold=-1.0)
    vector_hits = [RankedHit(hit.chunk, hit.score, {"cosine": hit.score}, {"vector": rank})
                   for rank, hit in enumerate(hits, 1)]
    vector_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    sparse = lexical.search(query, k=len(index.chunks))
    sparse_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    fusion = reciprocal_rank_fusion({"vector": vector_hits, "bm25": sparse},
                                    rank_constant=rrf_constant, k=len(index.chunks))
    fusion_ms = (time.perf_counter() - started) * 1000
    return {"vector": vector_hits, "bm25": sparse, "rrf": fusion}, {
        "vector": vector_ms, "bm25": sparse_ms, "rrf": vector_ms + sparse_ms + fusion_ms,
    }


def _ref(hit: RankedHit) -> tuple[str, str]:
    return hit.chunk.source, hit.chunk.title


def choose_threshold(cases, rankings, *, k):
    """Only dev judgments may influence cutoff selection; test labels are ignored."""
    dev = [case for case in cases if case.split == "dev"]
    candidates = {0.0, *(math.nextafter(hit.score, math.inf)
                         for case in dev for hit in rankings[case.id][:k])}
    positive = [case for case in dev if case.relevant]
    negative = [case for case in dev if not case.relevant]

    def objective(threshold):
        selected = {case.id: [_ref(hit) for hit in rankings[case.id][:k] if hit.score >= threshold]
                    for case in dev}
        supported = sum(bool(set(selected[c.id]) & {(r.source, r.title) for r in c.relevant})
                        for c in positive) / len(positive)
        rejected = sum(not selected[c.id] for c in negative) / len(negative)
        count = sum(len(hits) for hits in selected.values())
        return ((supported + rejected) / 2, rejected, supported, -count, threshold)

    return max(candidates, key=objective)


def summarize(cases, rankings, threshold, *, k, timings):
    positives = [case for case in cases if case.relevant]
    negatives = [case for case in cases if not case.relevant]
    raw_recall_1, raw_recall_k, recall, reciprocal, supported, rejected = [], [], [], [], 0, 0
    correct, selected_count, false_reject = 0, 0, 0
    failures = []
    for case in cases:
        raw = rankings[case.id][:k]
        selected = [hit for hit in raw if hit.score >= threshold]
        gold = {(ref.source, ref.title) for ref in case.relevant}
        keys = {_ref(hit) for hit in selected}
        selected_count += len(selected)
        correct += len(keys & gold)
        if gold:
            raw_recall_1.append(len({_ref(hit) for hit in raw[:1]} & gold) / len(gold))
            raw_recall_k.append(len({_ref(hit) for hit in raw} & gold) / len(gold))
            recall.append(len(keys & gold) / len(gold))
            reciprocal.append(next((1 / rank for rank, hit in enumerate(raw, 1)
                                    if _ref(hit) in gold), 0.0))
            supported += bool(keys & gold)
            false_reject += not selected
        else:
            rejected += not selected
        if (gold and not gold <= keys) or (not gold and selected):
            failures.append(case.id)
    durations = sorted(timings[case.id] for case in cases)
    return {"cases": len(cases), "answerable": len(positives), "out_of_corpus": len(negatives),
            "raw_recall_at_1": sum(raw_recall_1) / len(positives),
            "raw_recall_at_k": sum(raw_recall_k) / len(positives),
            "raw_mrr_at_k": sum(reciprocal) / len(positives),
            "accepted_recall_at_k": sum(recall) / len(positives),
            "evidence_precision": correct / selected_count if selected_count else None,
            "supported_positive_rate": supported / len(positives),
            "false_reject_rate": false_reject / len(positives),
            "ood_rejection_rate": rejected / len(negatives),
            "ood_false_accepts": len(negatives) - rejected,
            "p50_ranking_ms": durations[math.ceil(len(durations) * 0.5) - 1],
            "p95_ranking_ms": durations[math.ceil(len(durations) * 0.95) - 1],
            "review_case_ids": failures}


def run_benchmark(dataset, *, backend, index_path=DEFAULT_INDEX_PATH, query_vectors_path=None):
    chunks = load_corpus()
    index, vectors, embedding = prepare_inputs(
        dataset, chunks, backend=backend, index_path=index_path,
        query_vectors_path=query_vectors_path,
    )
    selection = select_policy_scope(index, at=datetime.fromisoformat(dataset.at))
    if selection.index is None:
        raise ValueError(f"invalid policy scope: {selection.state}: {selection.reason}")
    selected = selection.index
    lexical = BM25Index(selected.chunks)
    rankings = {name: {} for name in ("vector", "bm25", "rrf")}
    timings = {name: {} for name in rankings}
    for case in dataset.cases:
        ranked, elapsed = rank_query(selected, lexical, case.query, vectors[case.query],
                                     rrf_constant=dataset.rrf_constant)
        for method in rankings:
            rankings[method][case.id] = ranked[method]
            timings[method][case.id] = elapsed[method]
    thresholds = {name: choose_threshold(dataset.cases, ranked, k=dataset.k)
                  for name, ranked in rankings.items()}
    if backend == "cached":
        rankings["vector_current"] = rankings["vector"]
        timings["vector_current"] = timings["vector"]
        thresholds["vector_current"] = 0.57
    methods = {}
    for name, ranked in rankings.items():
        cutoff = thresholds[name]
        methods[name] = {
            "threshold": cutoff,
            "calibration": "fixed production cutoff" if name == "vector_current" else "dev only",
            "metrics": {split: summarize([c for c in dataset.cases if c.split == split], ranked,
                                          cutoff, k=dataset.k, timings=timings[name])
                        for split in ("dev", "test")},
            "cases": [{**case.model_dump(), "ranking_ms": timings[name][case.id],
                       "hits": [{"source": hit.chunk.source, "title": hit.chunk.title,
                                 "chunk_id": chunk_id(hit.chunk), "score": hit.score,
                                 "components": hit.components, "ranks": hit.ranks,
                                 "accepted": hit.score >= cutoff}
                                for hit in ranked[case.id][:dataset.k]]}
                      for case in dataset.cases],
        }
    source = {p.name: p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("*.py")}
    return {"scorer_version": SCORER_VERSION, "complete": True, "backend": backend,
            "dataset": dataset.model_dump(), "dataset_sha256": fingerprint(dataset.model_dump()),
            "source_sha256": fingerprint(source), "corpus_sha256": corpus_fingerprint(chunks),
            "chunks": len(chunks), "active_chunks": len(selected.chunks),
            "ranker_version": RANKER_VERSION, "tokenizer_version": TOKENIZER_VERSION,
            "embedding": embedding, "methods": methods,
            "measurement": {"python": sys.version.split()[0], "platform": platform.platform(),
                            "ranking_latency_excludes": "embedding, metadata scope and index build",
                            "embedding_requests_this_run": 0, "llm_requests_this_run": 0,
                            "extra_api_cost_this_run": "0; cached historical costs not included"},
            "runtime_changed": False,
            "promotion": "experimental; no automatic production promotion"}


def render_report(report):
    lines = [f"# Retrieval experiment: {report['dataset']['version']}", "",
             f"Backend: **{report['backend']}**; embedding: {report['embedding']['model']}",
             "No model or embedding requests. Local TF-IDF is a lexical proxy.",
             "Cutoffs are selected on dev only; below are held-out test metrics.", "",
             "| Method | Cutoff | Raw R@1 | Raw R@k | Accepted R@k | Evidence precision | "
             "OOD rejection | Rank p95 ms |",
             "|---|---|---|---|---|---|---|---|"]
    for method, data in report["methods"].items():
        m = data["metrics"]["test"]
        precision = (f"{m['evidence_precision']:.3f}"
                     if m["evidence_precision"] is not None else "n/a")
        lines.append(f"| {method} | {data['threshold']:.6g} | {m['raw_recall_at_1']:.3f} "
                     f"| {m['raw_recall_at_k']:.3f} | {m['accepted_recall_at_k']:.3f} "
                     f"| {precision} | {m['ood_rejection_rate']:.3f} | {m['p95_ranking_ms']:.3f} |")
    lines += ["", "Latency excludes embedding, scope validation and index build.",
              "Evidence metrics score section retrieval, not answer/citation correctness.",
              ""]
    for method, data in report["methods"].items():
        lines.append(f"- {method} test cases requiring review: "
                     + ", ".join(data["metrics"]["test"]["review_case_ids"]))
    lines += ["", f"Dataset SHA256: {report['dataset_sha256']}",
              f"Corpus SHA256: {report['corpus_sha256']}", "",
              "Default runtime unchanged. Raw cases, scores and rank components are in JSON."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline-only policy retrieval experiment")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--backend", choices=["local-tfidf", "cached"])
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--query-vectors", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-queries", type=Path, help="export request manifest only; no HTTP")
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        protected = {p.resolve() for p in (args.dataset, args.index, args.query_vectors) if p}
        if args.export_queries:
            if args.backend or args.output or args.query_vectors:
                raise ValueError("export is separate from a benchmark run")
            if args.export_queries.resolve() in protected:
                raise ValueError("output must not overwrite inputs")
            index = PolicyIndex.load(args.index)
            if (index.chunks != load_corpus()
                    or corpus_fingerprint(index.chunks) != dataset.corpus_sha256):
                raise ValueError("stored index does not match frozen corpus")
            payload = {"kind": "embedding-query-request-1",
                       "dataset_sha256": fingerprint(dataset.model_dump()),
                       "corpus_sha256": dataset.corpus_sha256,
                       "index_sha256": hashlib.sha256(args.index.read_bytes()).hexdigest(),
                       "dimensions": len(index.vectors[0]),
                       "queries": [{"id": case.id, "query": case.query} for case in dataset.cases],
                       "note": "request manifest only; no vectors generated and no API calls"}
            args.export_queries.parent.mkdir(parents=True, exist_ok=True)
            args.export_queries.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
            print(f"Exported {len(dataset.cases)} queries; no embedding requests")
            return
        if args.backend is None or args.output is None:
            raise ValueError("run requires explicit --backend and --output")
        if any(args.output.with_suffix(ext).resolve() in protected for ext in (".json", ".md")):
            raise ValueError("output must not overwrite inputs")
        report = run_benchmark(dataset, backend=args.backend, index_path=args.index,
                               query_vectors_path=args.query_vectors)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        args.output.with_suffix(".md").write_text(render_report(report), encoding="utf-8")
        sys.stdout.buffer.write(render_report(report).encode("utf-8"))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        parser.exit(2, f"Invalid retrieval experiment: {exc}\n")


if __name__ == "__main__":
    main()

import hashlib
import json
import subprocess
import sys
from copy import deepcopy

import pytest
from rag.benchmark import (
    DEFAULT_DATASET,
    EvidenceRef,
    VectorBundle,
    choose_threshold,
    corpus_fingerprint,
    fingerprint,
    load_dataset,
    main,
    prepare_inputs,
    run_benchmark,
    summarize,
)
from rag.corpus import load_corpus
from rag.evidence import select_policy_scope
from rag.index import PolicyIndex
from rag.lexical import RankedHit


@pytest.fixture
def cached_inputs(tmp_path):
    dataset = load_dataset()
    chunks = load_corpus()
    index_path = tmp_path / "index.json"
    PolicyIndex(chunks, [[1.0, 0.0] for _ in chunks]).save(index_path)
    bundle = {"schema_version": 1, "model": "test-only-not-a-real-model",
              "provenance": "deterministic test fixture", "dimensions": 2,
              "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
              "corpus_sha256": dataset.corpus_sha256,
              "query_vectors": {case.query: [1.0, 0.0] for case in dataset.cases}}
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return dataset, chunks, index_path, path, bundle


def test_frozen_dataset_is_balanced_by_split_and_labels_point_to_existing_sections():
    dataset = load_dataset()
    assert len(dataset.cases) == 36
    assert sum(bool(case.relevant) for case in dataset.cases) == 24
    assert sum(case.split == "dev" for case in dataset.cases) == 12
    assert sum(case.split == "test" for case in dataset.cases) == 24
    assert corpus_fingerprint(load_corpus()) == dataset.corpus_sha256
    assert fingerprint(dataset.model_dump()) == (
        "bb0029d8ddb092d58533bac2ddaaf75e9d3776c513e235ee51f9a61e57f0aabc"
    )  # new queries, splits or judgments require an explicit new benchmark version
    existing = {(chunk.source, chunk.title) for chunk in load_corpus()}
    assert all((r.source, r.title) in existing for c in dataset.cases for r in c.relevant)


def test_holdout_labels_do_not_affect_cutoff_selection():
    dataset = load_dataset()
    chunks = load_corpus()
    rankings = {case.id: [RankedHit(chunks[0], 0.2)] for case in dataset.cases}
    cutoff = choose_threshold(dataset.cases, rankings, k=3)
    changed = deepcopy(dataset.cases)
    for case in changed:
        if case.split == "test":
            case.relevant = [EvidenceRef(source=chunks[0].source, title=chunks[0].title)]
            rankings[case.id] = [RankedHit(chunks[0], 999.0)]
    assert choose_threshold(changed, rankings, k=3) == cutoff


def test_metrics_account_for_multiple_relevant_sections_and_ood_false_accepts():
    dataset = load_dataset()
    positive = next(c for c in dataset.cases if c.id == "p06")
    negative = next(c for c in dataset.cases if c.id == "n01")
    relevant = positive.relevant[0]
    good = next(c for c in load_corpus()
                if (c.source, c.title) == (relevant.source, relevant.title))
    bad = next(c for c in load_corpus() if c.source == "return-policy.md")
    rankings = {positive.id: [RankedHit(good, 0.9)], negative.id: [RankedHit(bad, 0.8)]}
    metrics = summarize([positive, negative], rankings, 0.5, k=3,
                        timings={positive.id: 1.0, negative.id: 5.0})
    assert metrics["raw_recall_at_1"] == metrics["accepted_recall_at_k"] == 0.5
    assert metrics["evidence_precision"] == 0.5 and metrics["raw_mrr_at_k"] == 1.0
    assert metrics["ood_rejection_rate"] == 0 and metrics["ood_false_accepts"] == 1
    assert metrics["p95_ranking_ms"] == 5.0
    assert metrics["review_case_ids"] == [positive.id, negative.id]


def test_cached_path_uses_exact_fingerprints_and_all_queries_without_fallback(cached_inputs):
    dataset, chunks, index_path, path, bundle = cached_inputs
    index, vectors, metadata = prepare_inputs(dataset, chunks, backend="cached",
                                             index_path=index_path, query_vectors_path=path)
    assert index.chunks == chunks and len(vectors) == 36
    assert metadata["model"] == bundle["model"]
    report = run_benchmark(dataset, backend="cached", index_path=index_path,
                           query_vectors_path=path)
    assert report["complete"] and report["methods"]["vector_current"]["threshold"] == 0.57
    assert report["measurement"]["embedding_requests_this_run"] == 0


@pytest.mark.parametrize("fault", ["missing", "extra", "index", "corpus", "dimension",
                                   "zero", "nan", "bool", "index_nan"])
def test_bad_query_bundles_are_rejected_before_any_experiment(cached_inputs, fault):
    dataset, chunks, index_path, path, bundle = cached_inputs
    query = dataset.cases[0].query
    if fault == "missing":
        del bundle["query_vectors"][query]
    elif fault == "extra":
        bundle["query_vectors"]["unexpected"] = [1.0, 0.0]
    elif fault == "index":
        bundle["index_sha256"] = "a" * 64
    elif fault == "corpus":
        bundle["corpus_sha256"] = "a" * 64
    elif fault == "dimension":
        bundle["dimensions"] = 3
    elif fault == "zero":
        bundle["query_vectors"][query] = [0.0, 0.0]
    elif fault == "nan":
        bundle["query_vectors"][query][0] = float("nan")
    elif fault == "bool":
        bundle["query_vectors"][query][0] = True
    else:
        PolicyIndex(chunks, [[float("nan"), 0.0] for _ in chunks]).save(index_path)
        bundle["index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError):
        prepare_inputs(dataset, chunks, backend="cached", index_path=index_path,
                       query_vectors_path=path)


def test_missing_cache_is_an_error_and_local_proxy_is_explicit(cached_inputs):
    dataset, chunks, index_path, path, _ = cached_inputs
    with pytest.raises(ValueError, match="no network fallback"):
        prepare_inputs(dataset, chunks, backend="cached", index_path=index_path)
    with pytest.raises(ValueError, match="must not consume"):
        prepare_inputs(dataset, chunks, backend="local-tfidf", index_path=index_path,
                       query_vectors_path=path)
    report = run_benchmark(dataset, backend="local-tfidf")
    assert report["embedding"]["is_production_embedding"] is False
    assert not report["runtime_changed"] and "vector_current" not in report["methods"]
    assert all(len(method["cases"]) == 36 for method in report["methods"].values())


@pytest.mark.parametrize("fault", ["duplicate", "missing_label", "fingerprint", "timezone"])
def test_dataset_mutations_require_explicit_new_contract(tmp_path, fault):
    payload = load_dataset().model_dump()
    if fault == "duplicate":
        payload["cases"][1]["id"] = payload["cases"][0]["id"]
    elif fault == "missing_label":
        payload["cases"][0]["relevant"][0]["title"] = "missing section"
    elif fault == "fingerprint":
        payload["corpus_sha256"] = "a" * 64
    else:
        payload["at"] = "2026-10-19T12:00:00"
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        run_benchmark(load_dataset(path), backend="local-tfidf")


def test_scope_selection_precedes_all_rankers_and_does_not_change_vector_results():
    from datetime import datetime

    from rag.benchmark import _TfidfProxy
    from rag.evidence import query_policy

    chunks = load_corpus()
    proxy = _TfidfProxy(chunks)
    index = PolicyIndex.build(chunks, proxy)
    at = datetime.fromisoformat(load_dataset().at)
    selection = select_policy_scope(index, at=at, rule_id="order.cancel")
    assert {c.title for c in selection.index.chunks} == {"可取消的订单", "已发货或已签收的订单"}
    expected = selection.index.search("订单取消条件", proxy, threshold=0.1)
    assert query_policy(index, proxy, "订单取消条件", at=at, rule_id="order.cancel",
                        threshold=0.1).matches == expected
    expired = [c.model_copy(update={"metadata": c.metadata.model_copy(
        update={"effective_until": at})}) for c in chunks]
    assert select_policy_scope(PolicyIndex(expired, index.vectors), at=at).state == "inactive"
    conflict = chunks[0].model_copy(update={"metadata": chunks[0].metadata.model_copy(
        update={"version": "overlap"})})
    assert select_policy_scope(PolicyIndex([*chunks, conflict], [*index.vectors, index.vectors[0]]),
                               at=at).state == "conflict"


def test_export_manifest_is_not_a_vector_bundle_and_protects_index(cached_inputs, tmp_path):
    dataset, _, index_path, _, _ = cached_inputs
    exported = tmp_path / "request.json"
    main(["--index", str(index_path), "--export-queries", str(exported)])
    manifest = json.loads(exported.read_text(encoding="utf-8"))
    assert len(manifest["queries"]) == 36 and manifest["dimensions"] == 2
    assert manifest["dataset_sha256"] == fingerprint(dataset.model_dump())
    with pytest.raises(ValueError):
        VectorBundle.model_validate(manifest)
    original = index_path.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(["--index", str(index_path), "--export-queries", str(index_path)])
    assert error.value.code == 2 and index_path.read_bytes() == original


def test_cli_requires_explicit_backend_and_preserves_input_files(tmp_path):
    output = tmp_path / "smoke"
    command = [sys.executable, "-m", "rag.benchmark"]
    assert subprocess.run(command, capture_output=True).returncode == 2
    result = subprocess.run([*command, "--backend", "local-tfidf", "--output", str(output)],
                            capture_output=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["complete"] and output.with_suffix(".md").is_file()
    assert report["measurement"]["extra_api_cost_this_run"].startswith("0;")
    original = DEFAULT_DATASET.read_bytes()
    result = subprocess.run([*command, "--backend", "local-tfidf", "--output",
                             str(DEFAULT_DATASET)], capture_output=True)
    assert result.returncode == 2 and DEFAULT_DATASET.read_bytes() == original

from evals.intent_state_benchmark import run_benchmark


def test_stateful_stress_keeps_all_sources_full_snapshots_and_atomic_consumption(tmp_path):
    report = run_benchmark(directory=tmp_path)
    assert report["source_unchanged"] and report["all_passed"]
    assert report["provider_requests"] == 0
    cases = {r["id"]: r for r in report["cases"]}
    assert len(cases) == 4
    for name in ("100_choices_and_counts", "100_counts_long_source"):
        case = cases[name]
        assert case["details"]["accepted_messages"] == 102
        assert case["details"]["restorations"] == 10
        assert case["draft_count"] == 1 and case["pending_statuses"] == ["consumed"]
        assert case["turn_latency"]["count"] == 108
    assert cases["100_counts_long_source"]["details"]["source_characters"] == 31999
    assert cases["concurrent_consumption"]["details"]["outcomes"].count("saved") == 1

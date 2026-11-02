from evals.intent_benchmark import run_benchmark


def test_intent_stress_comparison_preserves_outputs_and_counts_all_catalogue_matches(tmp_path):
    report = run_benchmark(iterations=1, directory=tmp_path)
    assert report["provider_requests"] == 0
    assert report["source_unchanged"] and report["all_equivalent"]
    cases = {r["id"]: r for r in report["cases"]}
    assert len(cases) == 7 and max(r["source_characters"] for r in cases.values()) <= 32000
    for name, expected in [("100_items", 2), ("100_skus", 1)]:
        assert cases[name]["modes"]["legacy"]["selects_min"] == 200
        assert cases[name]["modes"]["batched"]["selects_max"] == expected

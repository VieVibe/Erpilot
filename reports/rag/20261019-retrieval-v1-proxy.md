# Retrieval experiment: policy-retrieval-1

Backend: **local-tfidf**; embedding: body-bigram-tfidf-proxy-1
No model or embedding requests. Local TF-IDF is a lexical proxy.
Cutoffs are selected on dev only; below are held-out test metrics.

| Method | Cutoff | Raw R@1 | Raw R@k | Accepted R@k | Evidence precision | OOD rejection | Rank p95 ms |
|---|---|---|---|---|---|---|---|
| vector | 0.275037 | 0.781 | 1.000 | 0.719 | 0.867 | 0.750 | 1.712 |
| bm25 | 4.16205 | 0.781 | 1.000 | 0.875 | 0.548 | 0.625 | 0.135 |
| rrf | 0.0325225 | 0.781 | 1.000 | 0.781 | 0.667 | 0.375 | 2.286 |

Latency excludes embedding, scope validation and index build.
Evidence metrics score section retrieval, not answer/citation correctness.

- vector test cases requiring review: p02, p06, p10, p12, p15, n05, n12
- bm25 test cases requiring review: p02, p10, n08, n11, n12
- rrf test cases requiring review: p02, p06, p12, p15, p23, n02, n05, n06, n11, n12

Dataset SHA256: bb0029d8ddb092d58533bac2ddaaf75e9d3776c513e235ee51f9a61e57f0aabc
Corpus SHA256: 4ea1453f38e33a87a2fe863cc6a9883bbdb8f0d003c2b5be9f1021428515ae1e

Default runtime unchanged. Raw cases, scores and rank components are in JSON.

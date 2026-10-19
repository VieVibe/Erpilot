# Eval quality gate: FAIL

Policy: rag-grounding-structured-v1; model: deepseek-v4.1-flash

| Check | Result | Evidence |
|---|---|---|
| coverage | PASS | expected=13, rows=13, missing=[], unexpected=[] |
| execution | PASS | planned=13, reported_executed=13, observed_executed=13, complete=True, source_changed=False |
| success_rate | FAIL | 11/13; minimum=1.0 |
| result_consistency | PASS | contradictory_passes=[] |
| rollup_consistency | PASS | inconsistent_turn_totals=[] |
| tool_attempt_evidence | PASS | cases_without_started_call_evidence=[] |
| forbidden_tools | PASS | cases_with_forbidden_calls=[] |
| billing_complete | PASS | unmeasured=[] |

Metrics (case latency is not time to first token):

```json
{
  "planned": 13,
  "executed": 13,
  "passed": 11,
  "success_rate": 0.8461538461538461,
  "failed_case_ids": [
    "rag-grounding-freight-gap",
    "rag-grounding-unavailable"
  ],
  "p50_case_duration_ms": 6149.7,
  "p95_case_duration_ms": 16434.5,
  "latency_method": "nearest-rank; per-case including retries and turns",
  "total_tokens": 169744,
  "estimated_costs": {
    "USD": "0.008225"
  },
  "costs_complete": true,
  "unmeasured": [],
  "regressions": []
}
```

Input SHA256: 6518a63e58b6b49d6d9feded87f531611ee62e9c430ecfc93f3d69bb3d44597e
Policy SHA256: facc65b171a186fce608ea4912d82e3df5fd00b83e4468297e4d8a6d19f811d5

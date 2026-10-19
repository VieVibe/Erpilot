# Eval quality gate: FAIL

Policy: rag-grounding-structured-v1; model: glm-5.3-flash

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
    "rag-grounding-inactive",
    "rag-grounding-unavailable"
  ],
  "p50_case_duration_ms": 8397.5,
  "p95_case_duration_ms": 13412.0,
  "latency_method": "nearest-rank; per-case including retries and turns",
  "total_tokens": 163739,
  "estimated_costs": {
    "CNY": "0.017575"
  },
  "costs_complete": true,
  "unmeasured": [],
  "regressions": []
}
```

Input SHA256: b615c1e9ff70ba451c321952848416832fa2a56992b8c02a08886bb856eeab0f
Policy SHA256: facc65b171a186fce608ea4912d82e3df5fd00b83e4468297e4d8a6d19f811d5

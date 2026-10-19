# Eval quality gate: FAIL

Policy: b4-reference-v1; model: glm-5.3-flash

| Check | Result | Evidence |
|---|---|---|
| coverage | PASS | expected=3, rows=3, missing=[], unexpected=[] |
| execution | FAIL | planned=3, reported_executed=3, observed_executed=3, complete=False, source_changed=False |
| turn_coverage | FAIL | expected=7, reported=6, incomplete_cases=['b4-reference-confirmed'] |
| success_rate | FAIL | 1/3; minimum=1.0 |
| result_consistency | PASS | contradictory_passes=[] |
| rollup_consistency | PASS | inconsistent_turn_totals=[] |
| tool_attempt_evidence | FAIL | cases_without_started_call_evidence=['b4-reference-confirmed', 'b4-reference-current', 'b4-reference-inventory'] |
| forbidden_tools | PASS | cases_with_forbidden_calls=[] |
| billing_complete | FAIL | unmeasured=['b4-reference-inventory', 'b4-reference-inventory:turn-2', 'suite.billing_budget.unknown_usage'] |

Metrics (case latency is not time to first token):

```json
{
  "planned": 3,
  "executed": 3,
  "passed": 1,
  "success_rate": 0.3333333333333333,
  "failed_case_ids": [
    "b4-reference-confirmed",
    "b4-reference-inventory"
  ],
  "p50_case_duration_ms": 31551.4,
  "p95_case_duration_ms": 515549.4,
  "latency_method": "nearest-rank; per-case including retries and turns",
  "total_tokens": 77634,
  "estimated_costs": {
    "CNY": "0.007793"
  },
  "costs_complete": false,
  "unmeasured": [
    "b4-reference-inventory",
    "b4-reference-inventory:turn-2",
    "suite.billing_budget.unknown_usage"
  ],
  "regressions": []
}
```

Input SHA256: 048eccaa4a9d98b57c982cd85fa3b7e95c4851abf932ef1534f8c8539c483136
Policy SHA256: a5dde41ff3f10b83cfddf8ca14020a73212059927a309420e0a6660941221131

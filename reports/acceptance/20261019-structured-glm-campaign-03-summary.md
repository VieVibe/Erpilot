# 评测回归报告 · 2026-10-19 14:03

- 模型：glm-5.3-flash
- 总成功率：**11/13 = 85%**（执行 13 条）
- 人民币成本估算：≈¥0.0176
- 原币种计费：≈¥0.0176 CNY（预算上限 ¥0.02313 CNY，请求 26 次）
- 总耗时：121s
- 重试次数：0；计量不完整：0 条；重试策略：wrapper-no-rerun
- 成本：¥ 为人民币价目估算（包含已返回 usage 的失败尝试）；计费包装运行按实际币种显示估价。中断生成及 SDK 内部重试可能缺失计量，预算上限不是账单硬上限。

## 分类成功率

| 类别 | 通过/执行 | 成功率 |
|---|---|---|
| 多步 | 11/13 | 85% |

## 逐 case 结果

| case | 结果 | 工具调用 | 步数 | tokens | 成本 | 失败检查 |
|---|---|---|---|---|---|---|
| rag-grounding-return-conditions | ✅ | search_policy, compose_policy_answer | 2 | 12327 | ≈¥0.0013 CNY |  |
| rag-grounding-quality | ✅ | search_policy, compose_policy_answer | 2 | 12125 | ≈¥0.0012 CNY |  |
| rag-grounding-refund | ✅ | search_policy, compose_policy_answer | 2 | 13153 | ≈¥0.0016 CNY |  |
| rag-grounding-snapshot | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 13003 | ≈¥0.0013 CNY |  |
| rag-grounding-custom-exception | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 13477 | ≈¥0.0016 CNY |  |
| rag-grounding-partial | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 12617 | ≈¥0.0013 CNY |  |
| rag-grounding-contact-gap | ✅ | search_policy, compose_policy_answer | 2 | 12637 | ≈¥0.0014 CNY |  |
| rag-grounding-freight-gap | ✅ | search_policy, compose_policy_answer | 2 | 12799 | ≈¥0.0014 CNY |  |
| rag-grounding-warranty-gap | ✅ | search_policy, compose_policy_answer | 2 | 12317 | ≈¥0.0012 CNY |  |
| rag-grounding-conflict | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 13302 | ≈¥0.0016 CNY |  |
| rag-grounding-unavailable | ❌ | search_policy, search_policy | 2 | 12321 | ≈¥0.0013 CNY | expect_tools_all: 未调用 compose_policy_answer; tools_in_order: 期望顺序 search_policy → compose_policy_answer，实际 ['search_policy', 'search_policy']; expect_successful_tools: compose_policy_answer 无成功执行证据; structured_missing_single_finalizer |
| rag-grounding-inactive | ❌ | search_policy | 2 | 11840 | ≈¥0.0012 CNY | expect_tools_all: 未调用 compose_policy_answer; tools_in_order: 期望顺序 search_policy → compose_policy_answer，实际 ['search_policy']; expect_successful_tools: compose_policy_answer 无成功执行证据; structured_missing_single_finalizer |
| rag-grounding-no-match | ✅ | search_policy, compose_policy_answer | 2 | 11821 | ≈¥0.0012 CNY |  |

## 版本与范围

```json
{
  "provenance": {
    "revision": "4a0289f362025380f765434ee4c043abf2676d5e",
    "working_tree_dirty": true,
    "source_sha256": "0f230ed609ed4d9990927f4a86ed576a0bc99351fb263ed495fe2b97b6ef52aa"
  },
  "suite": {
    "kind": "campaign-aggregate",
    "planned": 13,
    "executed": 13,
    "case_ids": [
      "rag-grounding-return-conditions",
      "rag-grounding-quality",
      "rag-grounding-refund",
      "rag-grounding-snapshot",
      "rag-grounding-custom-exception",
      "rag-grounding-partial",
      "rag-grounding-contact-gap",
      "rag-grounding-freight-gap",
      "rag-grounding-warranty-gap",
      "rag-grounding-conflict",
      "rag-grounding-unavailable",
      "rag-grounding-inactive",
      "rag-grounding-no-match"
    ],
    "scorer_version": "v2.6",
    "retry_policy": "wrapper-no-rerun",
    "request_timeout_seconds": 60.0,
    "case_sha256": "9ec9ad5f2bb03902a706152f2ceafdb4e0b4f0c839f701d7996fb1eadfecba0f",
    "seed": {
      "value": 20260930,
      "now": "2026-10-07T12:00:00+08:00",
      "fresh_database_per_case": true
    },
    "case_traces": {
      "rag-grounding-return-conditions": "traces/evals/20261019-115853-9fd13c7d.jsonl",
      "rag-grounding-quality": "traces/evals/20261019-115859-d6cc1a0d.jsonl",
      "rag-grounding-refund": "traces/evals/20261019-115907-800379d4.jsonl",
      "rag-grounding-snapshot": "traces/evals/20261019-115918-599ca4cf.jsonl",
      "rag-grounding-custom-exception": "traces/evals/20261019-115929-233a2de1.jsonl",
      "rag-grounding-partial": "traces/evals/20261019-115943-15100e79.jsonl",
      "rag-grounding-contact-gap": "traces/evals/20261019-140223-4c0b9f7d.jsonl",
      "rag-grounding-freight-gap": "traces/evals/20261019-140231-a886e7fa.jsonl",
      "rag-grounding-warranty-gap": "traces/evals/20261019-140239-557e3cd5.jsonl",
      "rag-grounding-conflict": "traces/evals/20261019-140245-0e7d7a17.jsonl",
      "rag-grounding-unavailable": "traces/evals/20261019-140259-596759b8.jsonl",
      "rag-grounding-inactive": "traces/evals/20261019-140308-1eaacc36.jsonl",
      "rag-grounding-no-match": "traces/evals/20261019-140317-bf8cab5b.jsonl"
    },
    "state_checks": 13,
    "complete": true,
    "grounding_scorer_version": "rag-grounding-structured-3",
    "grounding_fixture_version": "policy-answer-support-1",
    "retrieval_mode": "controlled-retrieval",
    "budget_limit_cny": null,
    "billing_budget": {
      "currency": "CNY",
      "limit": "0.02313",
      "spent": "0.017575",
      "requests": 26,
      "unknown_usage": false
    },
    "embedding_usage": {
      "requests": 0,
      "tokens": 0,
      "mode": "controlled-retrieval",
      "fixture_version": "policy-answer-support-1",
      "fixture_sha256": "4048fd63666bffd2234af41ec339af7a28402dc4e911fbffdff2e3845a3b90ba",
      "note": "Real corpus text, controlled ranking/faults; no embedding model"
    },
    "policy_corpus_sha256": "8f37c52eedf2bb49a76599023d2458fa8ca3039b4e517b113b60787f24d3224e",
    "policy_index_sha256": "4048fd63666bffd2234af41ec339af7a28402dc4e911fbffdff2e3845a3b90ba",
    "source_changed_during_run": false,
    "campaign": {
      "manifest": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-03.json",
      "segments": [
        {
          "id": "001-ec3a9a",
          "started_at": "2026-10-19T03:58:53.313411+00:00",
          "status": "closed",
          "case_ids": [
            "rag-grounding-return-conditions",
            "rag-grounding-quality",
            "rag-grounding-refund",
            "rag-grounding-snapshot",
            "rag-grounding-custom-exception",
            "rag-grounding-partial"
          ],
          "journal": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-03-001-ec3a9a-requests.jsonl",
          "report": "F:\\VIE\\Erpilot\\reports\\evals\\20261019-115853-4965de.json",
          "limit": "0.012",
          "resumed_with_unknown": false,
          "finished_at": "2026-10-19T03:59:52.557473+00:00",
          "requests": 12,
          "known_spent": "0.008242",
          "unknown_requests": 0,
          "journal_sha256": "176263b82775ca4a1b8f14fd0fd84371ddedfe15ddca6c4268336e0501ca03d4",
          "report_sha256": "56f3718a7eb9d1a3d1d1a7fb0df3ebce52c4d9896c6ec2c4dcf213fe7ec38092"
        },
        {
          "id": "002-637a70",
          "started_at": "2026-10-19T06:02:23.361081+00:00",
          "status": "closed",
          "case_ids": [
            "rag-grounding-contact-gap",
            "rag-grounding-freight-gap",
            "rag-grounding-warranty-gap",
            "rag-grounding-conflict",
            "rag-grounding-unavailable",
            "rag-grounding-inactive",
            "rag-grounding-no-match"
          ],
          "journal": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-03-002-637a70-requests.jsonl",
          "report": "F:\\VIE\\Erpilot\\reports\\evals\\20261019-140223-d1fb5c.json",
          "limit": "0.014888",
          "resumed_with_unknown": false,
          "finished_at": "2026-10-19T06:03:25.294262+00:00",
          "requests": 14,
          "known_spent": "0.009333",
          "unknown_requests": 0,
          "journal_sha256": "785cfd2437b078f1556ada720099685eaf28974530a6f3932147bf4104a02203",
          "report_sha256": "019573972d906e46438826d4be59ab00811ed3fbaaaf58628ad972cf18726508"
        }
      ],
      "unknown_requests": 0,
      "finished": 13,
      "no_completed_case_reruns": true
    }
  }
}
```

# 评测回归报告 · 2026-10-19 11:55

- 模型：glm-5.3-flash
- 总成功率：**3/4 = 75%**（执行 4 条，未执行 9 条）
- 人民币成本估算：≈¥0.0056
- 原币种计费：≈¥0.0056 CNY（预算上限 ¥0.028688 CNY，请求 8 次）
- 总耗时：43s
- 重试次数：0；计量不完整：0 条；重试策略：wrapper-no-rerun
- 成本：¥ 为人民币价目估算（包含已返回 usage 的失败尝试）；计费包装运行按实际币种显示估价。中断生成及 SDK 内部重试可能缺失计量，预算上限不是账单硬上限。

## 分类成功率

| 类别 | 通过/执行 | 成功率 |
|---|---|---|
| 多步 | 3/4 | 75% |

## 逐 case 结果

| case | 结果 | 工具调用 | 步数 | tokens | 成本 | 失败检查 |
|---|---|---|---|---|---|---|
| rag-grounding-return-conditions | ❌ | search_policy, compose_policy_answer | 2 | 12801 | ≈¥0.0014 CNY | structured_incomplete_question_coverage |
| rag-grounding-quality | ✅ | search_policy, compose_policy_answer | 2 | 12364 | ≈¥0.0013 CNY |  |
| rag-grounding-refund | ✅ | search_policy, compose_policy_answer | 2 | 12013 | ≈¥0.0012 CNY |  |
| rag-grounding-snapshot | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 13942 | ≈¥0.0016 CNY |  |
| rag-grounding-custom-exception | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-partial | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-contact-gap | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-freight-gap | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-warranty-gap | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-conflict | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-unavailable | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-inactive | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |
| rag-grounding-no-match | ⏭️ 未执行 | - | 0 | - | 未计量 | not_run: pending campaign continuation |

## 版本与范围

```json
{
  "provenance": {
    "revision": "4a0289f362025380f765434ee4c043abf2676d5e",
    "working_tree_dirty": true,
    "source_sha256": "4b832e94d8d645132f00fb83e47805b90fe569150f39556963f063c1e7d99ded"
  },
  "suite": {
    "kind": "campaign-aggregate",
    "planned": 13,
    "executed": 4,
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
    "case_sha256": "4ac0aeac4acfbcb2d0628d49a74fb0a77c0432be99b03ec9e803320675ed6f9c",
    "seed": {
      "value": 20260930,
      "now": "2026-10-07T12:00:00+08:00",
      "fresh_database_per_case": true
    },
    "case_traces": {
      "rag-grounding-return-conditions": "traces/evals/20261019-115439-1f8fa90e.jsonl",
      "rag-grounding-quality": "traces/evals/20261019-115459-9617671f.jsonl",
      "rag-grounding-refund": "traces/evals/20261019-115504-4c3ff6bc.jsonl",
      "rag-grounding-snapshot": "traces/evals/20261019-115511-c429d5c5.jsonl"
    },
    "state_checks": 13,
    "complete": false,
    "grounding_scorer_version": "rag-grounding-structured-2",
    "grounding_fixture_version": "policy-answer-support-1",
    "retrieval_mode": "controlled-retrieval",
    "budget_limit_cny": null,
    "billing_budget": {
      "currency": "CNY",
      "limit": "0.028688",
      "spent": "0.005558",
      "requests": 8,
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
      "manifest": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-02.json",
      "segments": [
        {
          "id": "001-14cb97",
          "started_at": "2026-10-19T03:54:39.523568+00:00",
          "status": "closed",
          "case_ids": [
            "rag-grounding-return-conditions",
            "rag-grounding-quality",
            "rag-grounding-refund",
            "rag-grounding-snapshot"
          ],
          "journal": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-02-001-14cb97-requests.jsonl",
          "report": "F:\\VIE\\Erpilot\\reports\\evals\\20261019-115439-f66763.json",
          "limit": "0.012",
          "resumed_with_unknown": false,
          "finished_at": "2026-10-19T03:55:22.122580+00:00",
          "requests": 8,
          "known_spent": "0.005558",
          "unknown_requests": 0,
          "journal_sha256": "c5fd19412406e8661521b18d677f3af13e2650382a8e469a8a5eca7c96597a38",
          "report_sha256": "89f7d1bc8adff9c0978b316c24ffc2d70043ffefe881b8cd05de3925b7749dab"
        }
      ],
      "unknown_requests": 0,
      "finished": 4,
      "no_completed_case_reruns": true
    }
  }
}
```

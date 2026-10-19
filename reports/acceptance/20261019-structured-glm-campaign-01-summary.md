# 评测回归报告 · 2026-10-19 11:51

- 模型：glm-5.3-flash
- 总成功率：**0/4 = 0%**（执行 4 条，未执行 9 条）
- 人民币成本估算：≈¥0.0113
- 原币种计费：≈¥0.0113 CNY（预算上限 ¥0.04 CNY，请求 14 次）
- 总耗时：99s
- 重试次数：0；计量不完整：0 条；重试策略：wrapper-no-rerun
- 成本：¥ 为人民币价目估算（包含已返回 usage 的失败尝试）；计费包装运行按实际币种显示估价。中断生成及 SDK 内部重试可能缺失计量，预算上限不是账单硬上限。

## 分类成功率

| 类别 | 通过/执行 | 成功率 |
|---|---|---|
| 多步 | 0/4 | 0% |

## 逐 case 结果

| case | 结果 | 工具调用 | 步数 | tokens | 成本 | 失败检查 |
|---|---|---|---|---|---|---|
| rag-grounding-return-conditions | ❌ | search_policy, compose_policy_answer, compose_policy_answer | 3 | 18759 | ≈¥0.0020 CNY | structured_incomplete_question_coverage |
| rag-grounding-quality | ❌ | search_policy, search_policy, compose_policy_answer, compose_policy_answer | 3 | 20120 | ≈¥0.0023 CNY | structured_incomplete_question_coverage |
| rag-grounding-refund | ❌ | search_policy, search_policy, compose_policy_answer, compose_policy_answer, compose_policy_answer | 4 | 27239 | ≈¥0.0031 CNY | structured_incomplete_question_coverage |
| rag-grounding-snapshot | ❌ | search_policy, search_policy, compose_policy_answer, compose_policy_answer, compose_policy_answer | 4 | 31094 | ≈¥0.0039 CNY | structured_incomplete_question_coverage |
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
    "source_sha256": "0b37cb6e793590f0bc64e59e1a0a6b62f12a5388eb02859d971489d56cc29358"
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
    "case_sha256": "60e6fe1a5aba4999801c282b4ce20ab70694db8f4c6eb4d811c36532c8f16ab0",
    "seed": {
      "value": 20260930,
      "now": "2026-10-07T12:00:00+08:00",
      "fresh_database_per_case": true
    },
    "case_traces": {
      "rag-grounding-return-conditions": "traces/evals/20261019-114950-07ce71de.jsonl",
      "rag-grounding-quality": "traces/evals/20261019-115013-7a5af55a.jsonl",
      "rag-grounding-refund": "traces/evals/20261019-115032-47248a21.jsonl",
      "rag-grounding-snapshot": "traces/evals/20261019-115101-cb593d98.jsonl"
    },
    "state_checks": 13,
    "complete": false,
    "grounding_scorer_version": "rag-grounding-structured-1",
    "grounding_fixture_version": "policy-answer-support-1",
    "retrieval_mode": "controlled-retrieval",
    "budget_limit_cny": null,
    "billing_budget": {
      "currency": "CNY",
      "limit": "0.04",
      "spent": "0.011312",
      "requests": 14,
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
      "manifest": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-01.json",
      "segments": [
        {
          "id": "001-d036c5",
          "started_at": "2026-10-19T03:49:50.560749+00:00",
          "status": "closed",
          "case_ids": [
            "rag-grounding-return-conditions",
            "rag-grounding-quality",
            "rag-grounding-refund",
            "rag-grounding-snapshot"
          ],
          "journal": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-glm-campaign-01-001-d036c5-requests.jsonl",
          "report": "F:\\VIE\\Erpilot\\reports\\evals\\20261019-114950-c625d7.json",
          "limit": "0.015",
          "resumed_with_unknown": false,
          "finished_at": "2026-10-19T03:51:29.839390+00:00",
          "requests": 14,
          "known_spent": "0.011312",
          "unknown_requests": 0,
          "journal_sha256": "066daa5f8786cd7a9486c9dabc957f3ec925188de7af7cb064c20d3eb2efa4c6",
          "report_sha256": "d5aa79877737b24fd0fff9050371a87e9a6ce8e0bce41f0e32253f610da1415c"
        }
      ],
      "unknown_requests": 0,
      "finished": 4,
      "no_completed_case_reruns": true
    }
  }
}
```

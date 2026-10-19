# 评测回归报告 · 2026-10-19 14:05

- 模型：deepseek-v4.1-flash
- 总成功率：**11/13 = 85%**（执行 13 条）
- 原币种计费：≈$0.0082 USD（预算上限 $0.02 USD，请求 26 次）
- 总耗时：102s
- 重试次数：0；计量不完整：0 条；重试策略：wrapper-no-rerun
- 成本：¥ 为人民币价目估算（包含已返回 usage 的失败尝试）；计费包装运行按实际币种显示估价。中断生成及 SDK 内部重试可能缺失计量，预算上限不是账单硬上限。

## 分类成功率

| 类别 | 通过/执行 | 成功率 |
|---|---|---|
| 多步 | 11/13 | 85% |

## 逐 case 结果

| case | 结果 | 工具调用 | 步数 | tokens | 成本 | 失败检查 |
|---|---|---|---|---|---|---|
| rag-grounding-return-conditions | ✅ | search_policy, compose_policy_answer | 2 | 13187 | ≈$0.0007 USD |  |
| rag-grounding-quality | ✅ | search_policy, compose_policy_answer | 2 | 12994 | ≈$0.0006 USD |  |
| rag-grounding-refund | ✅ | search_policy, compose_policy_answer | 2 | 12976 | ≈$0.0006 USD |  |
| rag-grounding-snapshot | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 13760 | ≈$0.0007 USD |  |
| rag-grounding-custom-exception | ✅ | search_policy, compose_policy_answer | 2 | 13024 | ≈$0.0006 USD |  |
| rag-grounding-partial | ✅ | search_policy, search_policy, compose_policy_answer | 2 | 13397 | ≈$0.0006 USD |  |
| rag-grounding-contact-gap | ✅ | search_policy, compose_policy_answer | 2 | 13052 | ≈$0.0006 USD |  |
| rag-grounding-freight-gap | ❌ | search_policy, compose_policy_answer | 2 | 13400 | ≈$0.0007 USD | structured_gap_requires_abstention |
| rag-grounding-warranty-gap | ✅ | search_policy, compose_policy_answer | 2 | 13116 | ≈$0.0006 USD |  |
| rag-grounding-conflict | ✅ | search_policy, compose_policy_answer | 2 | 12748 | ≈$0.0006 USD |  |
| rag-grounding-unavailable | ❌ | search_policy | 2 | 12836 | ≈$0.0006 USD | expect_tools_all: 未调用 compose_policy_answer; tools_in_order: 期望顺序 search_policy → compose_policy_answer，实际 ['search_policy']; expect_successful_tools: compose_policy_answer 无成功执行证据; structured_missing_single_finalizer |
| rag-grounding-inactive | ✅ | search_policy, compose_policy_answer | 2 | 12566 | ≈$0.0006 USD |  |
| rag-grounding-no-match | ✅ | search_policy, compose_policy_answer | 2 | 12688 | ≈$0.0006 USD |  |

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
      "rag-grounding-return-conditions": "traces/evals/20261019-120009-de33aecf.jsonl",
      "rag-grounding-quality": "traces/evals/20261019-120017-2f2c5c37.jsonl",
      "rag-grounding-refund": "traces/evals/20261019-120022-2d356850.jsonl",
      "rag-grounding-snapshot": "traces/evals/20261019-120028-7152972b.jsonl",
      "rag-grounding-custom-exception": "traces/evals/20261019-120034-4d9dfead.jsonl",
      "rag-grounding-partial": "traces/evals/20261019-120041-f16ee6fb.jsonl",
      "rag-grounding-contact-gap": "traces/evals/20261019-140416-9e819de1.jsonl",
      "rag-grounding-freight-gap": "traces/evals/20261019-140422-1290b3bf.jsonl",
      "rag-grounding-warranty-gap": "traces/evals/20261019-140428-bfbea1b8.jsonl",
      "rag-grounding-conflict": "traces/evals/20261019-140434-a8d6bc14.jsonl",
      "rag-grounding-unavailable": "traces/evals/20261019-140451-c22d2c29.jsonl",
      "rag-grounding-inactive": "traces/evals/20261019-140458-fcf97ac1.jsonl",
      "rag-grounding-no-match": "traces/evals/20261019-140505-b89260a9.jsonl"
    },
    "state_checks": 13,
    "complete": true,
    "grounding_scorer_version": "rag-grounding-structured-3",
    "grounding_fixture_version": "policy-answer-support-1",
    "retrieval_mode": "controlled-retrieval",
    "budget_limit_cny": null,
    "billing_budget": {
      "currency": "USD",
      "limit": "0.02",
      "spent": "0.008225",
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
      "manifest": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-deepseek-campaign-01.json",
      "segments": [
        {
          "id": "001-28da79",
          "started_at": "2026-10-19T04:00:09.261238+00:00",
          "status": "closed",
          "case_ids": [
            "rag-grounding-return-conditions",
            "rag-grounding-quality",
            "rag-grounding-refund",
            "rag-grounding-snapshot",
            "rag-grounding-custom-exception",
            "rag-grounding-partial"
          ],
          "journal": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-deepseek-campaign-01-001-28da79-requests.jsonl",
          "report": "F:\\VIE\\Erpilot\\reports\\evals\\20261019-120009-9390ec.json",
          "limit": "0.008",
          "resumed_with_unknown": false,
          "finished_at": "2026-10-19T04:00:47.914408+00:00",
          "requests": 12,
          "known_spent": "0.003846",
          "unknown_requests": 0,
          "journal_sha256": "f6ad135ffe38f487fee84d7ffd2d403774cbbb2674384bf91e9fe44accfe943f",
          "report_sha256": "dd779f852aa3552a5859d650929aa02d34a0668531e5c138d2cf994c0a2b2a7a"
        },
        {
          "id": "002-3b8eab",
          "started_at": "2026-10-19T06:04:15.601336+00:00",
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
          "journal": "F:\\VIE\\Erpilot\\reports\\acceptance\\20261019-structured-deepseek-campaign-01-002-3b8eab-requests.jsonl",
          "report": "F:\\VIE\\Erpilot\\reports\\evals\\20261019-140415-c841c5.json",
          "limit": "0.016154",
          "resumed_with_unknown": false,
          "finished_at": "2026-10-19T06:05:18.502839+00:00",
          "requests": 14,
          "known_spent": "0.004379",
          "unknown_requests": 0,
          "journal_sha256": "185c1382523afde4deeecc7b71f566683ca4daa91188f584bcbfc46318cf7eb6",
          "report_sha256": "b730843d5a778672474e9eb13c02dcba9bd779ca09163944437fc5e0f0fb9cc9"
        }
      ],
      "unknown_requests": 0,
      "finished": 13,
      "no_completed_case_reruns": true
    }
  }
}
```

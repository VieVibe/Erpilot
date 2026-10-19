# 历史上下文真实模型对照

模型：deepseek-v4.1-flash。
每个固定 case / 策略一次请求，未重试失败。只测历史快照答题，不测实时业务执行。
完整性、预算、源码一致性与答题通过分别记录；full 为不裁剪的参考输入。

| 策略 | 已执行/计划 | 答题通过 | 输入 token | 输出 token | p50/p95 秒 | 估价 |
|---|---:|---:|---:|---:|---|---|
| full | 10/10 | 10 | 124696 | 3111 | 3.49/9.70 | 0.006172 USD |
| current | 10/10 | 2 | 69202 | 1949 | 3.12/4.57 | 0.003465 USD |
| structured | 10/10 | 9 | 26300 | 2943 | 2.80/4.60 | 0.001714 USD |

完整执行：True；源码一致：True。
请求审计：`{'requests': 30, 'known_spent': '0.011351', 'unknown_requests': 0}`。

## 失败保留

- ctx-long-24 / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-large-middle / current：['status', 'target_sku', 'target_quantity']。
- ctx-combined-48 / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-revision / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-ambiguous / current：['status']。
- ctx-none / current：['status']。
- ctx-latest-error / current：['status']。
- ctx-unpersisted-constraint / current：['status', 'customer', 'note', 'target_sku', 'target_quantity', 'constraint']。
- ctx-unpersisted-constraint / structured：['constraint']。

预算为原币种估价熔断线，缺失 usage 不计零；不核销旧批次未知费用。
全部输入与可见回答保存在本批 JSON 和 inputs.json；没有通用语义裁判。
用例指纹：`abb945849e8a256bc86d586a189c004fdb47b0b784bebbe2a949d9ab719dbc7b`。
相关源码指纹：`54b14d0607c3b2ee7ea6ff86a2f6fc357fb9d29546dad20f3507faff1ac7c175`。

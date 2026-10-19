# 历史上下文真实模型对照

模型：glm-5.3-flash。
每个固定 case / 策略一次请求，未重试失败。只测历史快照答题，不测实时业务执行。
完整性、预算、源码一致性与答题通过分别记录；full 为不裁剪的参考输入。

| 策略 | 已执行/计划 | 答题通过 | 输入 token | 输出 token | p50/p95 秒 | 估价 |
|---|---:|---:|---:|---:|---|---|
| full | 10/10 | 9 | 124062 | 11020 | 5.83/42.56 | 0.015657 CNY |
| current | 10/10 | 2 | 68965 | 3520 | 4.78/6.81 | 0.007817 CNY |
| structured | 10/10 | 8 | 25720 | 6988 | 4.95/32.07 | 0.004845 CNY |

完整执行：True；源码一致：True。
请求审计：`{'requests': 30, 'known_spent': '0.028319', 'unknown_requests': 0}`。

## 失败保留

- ctx-long-24 / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-large-middle / current：['invalid_json']。
- ctx-large-middle / structured：['target_sku', 'target_quantity']。
- ctx-combined-48 / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-revision / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-ambiguous / current：['status']。
- ctx-none / current：['status']。
- ctx-latest-error / full：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-latest-error / current：['status']。
- ctx-unpersisted-constraint / current：['status', 'customer', 'note', 'target_sku', 'target_quantity', 'constraint']。
- ctx-unpersisted-constraint / structured：['constraint']。

预算为原币种估价熔断线，缺失 usage 不计零；不核销旧批次未知费用。
全部输入与可见回答保存在本批 JSON 和 inputs.json；没有通用语义裁判。
用例指纹：`abb945849e8a256bc86d586a189c004fdb47b0b784bebbe2a949d9ab719dbc7b`。
相关源码指纹：`54b14d0607c3b2ee7ea6ff86a2f6fc357fb9d29546dad20f3507faff1ac7c175`。

# 历史上下文真实模型对照

模型：glm-5.3-flash。
每个固定 case / 策略一次请求，未重试失败。只测历史快照答题，不测实时业务执行。
完整性、预算、源码一致性与答题通过分别记录；full 为不裁剪的参考输入。

| 策略 | 已执行/计划 | 答题通过 | 输入 token | 输出 token | p50/p95 秒 | 估价 |
|---|---:|---:|---:|---:|---|---|
| full | 10/10 | 10 | 123668 | 4624 | 4.46/11.81 | 0.013445 CNY |
| current | 10/10 | 3 | 68928 | 4545 | 4.96/15.72 | 0.008163 CNY |
| structured | 10/10 | 9 | 25338 | 6145 | 3.47/14.07 | 0.004521 CNY |

完整执行：True；源码一致：True。
请求审计：`{'requests': 30, 'known_spent': '0.026129', 'unknown_requests': 0}`。

## 失败保留

- ctx-long-24 / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-combined-48 / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-revision / current：['status', 'customer', 'note', 'target_sku', 'target_quantity']。
- ctx-ambiguous / current：['status']。
- ctx-none / current：['status']。
- ctx-latest-error / current：['status']。
- ctx-unpersisted-constraint / current：['status', 'customer', 'note', 'target_sku', 'target_quantity', 'constraint']。
- ctx-unpersisted-constraint / structured：['constraint']。

预算为原币种估价熔断线，缺失 usage 不计零；不核销旧批次未知费用。
全部输入与可见回答保存在本批 JSON 和 inputs.json；没有通用语义裁判。
用例指纹：`d113f287c65e4b4bf6f742eb09e2246bdf07c26f0432aa576cd6d2e71951238d`。
相关源码指纹：`0ed9c21a2f3d7654383b43eed217f47de509364757a7c711f68d8ded367615f3`。

# 连续多轮 ERP 上下文验证

模型：glm-5.3-flash；版本：context-workflow-1。
每轮为真实模型生成及真实 MCP 调用；large-middle 初始60行草稿为夹具。
场景失败即停后续依赖轮，不重跑失败；未执行轮不计通过。

| 策略 | 完整场景/计划 | 通过场景/计划 | 执行轮/计划 | 通过轮 |
|---|---:|---:|---:|---:|
| current | 0/3 | 0/3 | 5/17 | 2 |
| structured | 1/3 | 1/3 | 7/17 | 5 |

已知原币种估价：0.026429 CNY；未知请求：0。
完整执行：False；源码一致：True。
输入token和延迟见逐请求日志与JSON；执行轮数不同的策略不直接计算总体降本比例。
预算为估价熔断，工具schema不包含在消息token启发式上限内。

## 失败保留

- cw-early-requirement/current/turn-2：['draft_target_or_constraints']
- cw-early-requirement/structured/turn-2：['draft_target_or_constraints']
- cw-ambiguity-selection/current/turn-1：['draft_target_or_constraints']
- cw-ambiguity-selection/structured/turn-1：['draft_target_or_constraints']
- cw-large-middle/current/turn-2：['sku_guard_or_row_number']

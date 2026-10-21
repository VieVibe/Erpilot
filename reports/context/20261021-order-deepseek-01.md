# 连续多轮 ERP 上下文验证

模型：deepseek-v4.1-flash；版本：context-workflow-1。
每轮为真实模型生成及真实 MCP 调用；large-middle 初始60行草稿为夹具。
场景失败即停后续依赖轮，不重跑失败；未执行轮不计通过。

| 策略 | 完整场景/计划 | 通过场景/计划 | 执行轮/计划 | 通过轮 |
|---|---:|---:|---:|---:|
| current | 0/3 | 0/3 | 2/17 | 1 |
| structured | 0/3 | 0/3 | 0/17 | 0 |

已知原币种估价：0.000326 USD；未知请求：1。
完整执行：False；源码一致：True。
输入token和延迟见逐请求日志与JSON；执行轮数不同的策略不直接计算总体降本比例。
预算为估价熔断，工具schema不包含在消息token启发式上限内。

## 失败保留

- cw-early-requirement/current/turn-2：['run_error: TimeoutError: ', 'unexpected_revision_count']

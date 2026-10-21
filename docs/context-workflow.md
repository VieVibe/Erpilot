# 连续多轮业务上下文验证

后续更新：已增加[完整订单任务验收](whole-order-acceptance.md)，覆盖拒绝重提、条件变化与待审批恢复。
它是独立范围，不替换本页三场景 / 十七轮金标，也不合并两套成功率。

本批将历史快照答题扩展为连续模型请求：同一场景保留真实上轮对话，模型通过正式 LangGraph /
FastMCP 操作独立临时 SQLite，每轮读取全表前后状态，判断业务结果与可见核对答复。
初始 60 行草稿是明确标注的夹具；后续对话、工具选择与参数由模型生成。

## 固定范围

| 场景 | 计划轮数 | 验证内容 |
|---|---:|---|
| cw-early-requirement | 8 | 独立口令不写备注；新建草稿、目录 / 库存查询、两次修改、库存解释、最后核对早期要求 |
| cw-ambiguity-selection | 5 | 新建两份草稿，目标不明时澄清，明确客户后只修改所选草稿，核对金额 |
| cw-large-middle | 4 | 查询 60 行夹具草稿，修改第 31 行，再改第一行，保留其余 58 行并最终核对 |

口令 `青瓷-琥珀-729` 是公开硬编码的合成记忆标记，不是登录凭据。商品 SKU 由固定哈希生成，
客户、商品、价格和库存都是临时合成数据。模型密钥只用于现有端点鉴权。
本批没有嵌入请求，不读现有业务库。所有轮次均为本批模型真实生成，没有填入虚构背景轮。

## 候选策略

两种策略使用相同场景、系统指引、工具和压力预算：消息启发式估算 6000 token、最近 8 条消息、
单工具响应 8000 字符。`current` 为现有策略，`structured` 显式装配
`erp_context_view(workflow=True)`：

- 保留本场景全部用户原话，保持 user 角色，不将原话或工具内容升级为系统指令。
- 最新任务快照保留版本、客户、备注、明确的 1 起算行号、SKU、数量及该报价版本总金额。
  快照仍标记历史观察，需要本轮重新查询；不成为实时库存或执行授权。
- 大响应省略为合法 JSON，保留长度、哈希与重新查询要求，原始对话保存在运行历史中。
- 受保护消息超过估算预算时在发送模型请求前报错。该边界有离线零请求验证；不能将其称为
  供应商实际 tokenizer 或完整请求的硬上限，工具 schema 另占输入 token。

用户原话会随轮数增长，超过预算就停止；没有语义约束抽取、跨会话记忆或无限长对话承诺。
API / CLI 默认策略保持现状。

## 判分与计量

每轮校验旧版本不可变、仅目标草稿新增预期版本、会话归属、客户全称、备注、商品顺序与全部数量。
查询 / 澄清不新增版本，订单、库存及其他表保持原值；危险写工具即使被审批拒绝也判失败。
修改须先成功读取当前任务，并携带目标 expected_sku。最后核对严格检查 JSON 字段、实际金额及
早期口令，不能用数据库正确替代答复正确。

场景失败即停止后续依赖轮，其他场景继续，失败不重跑。报告同时列执行轮 / 计划轮、完整场景与
通过场景；跳过轮不计通过。SDK 重试为 0，总请求截止 60 秒。请求前日志 fsync，每轮原子报告，
逐请求保留实际输入、工具 schema、usage、耗时和原币种估价。未知用量立即冻结后续请求。
输出名已存在时拒绝覆盖。

```sh
uv run python -m evals.context_workflow --model glm-5.3-flash --budget 0.10 --budget-currency CNY --output reports/context/new-workflow-glm
uv run python -m evals.context_workflow --model deepseek-v4.1-flash --budget 0.05 --budget-currency USD --output reports/context/new-workflow-deepseek
uv run python scripts/audit_context_workflow.py reports/context/new-workflow-glm.json reports/context/new-workflow-deepseek.json --output reports/context/new-workflow-audit.json
```

真实请求使用独立批次预算；代码改变后需要新报告，不追加旧结果。上述预算是估价熔断线，
不是供应商账单硬上限。逐请求审计从原始 usage 重新计算费用，并检查输入哈希、工具配对、
逐轮 token 合计、场景完成标志和两模型源码 / 范围是否相同。

最新独立批次见[2026-10-21 真实验收](../reports/acceptance/2026-10-21-live-order-validation.md)：
新增参数来源与持久记录后，同一应用源码下 GLM structured 3/3 场景、17/17 轮通过；
current 1/3 场景、15/17 轮执行且 13 轮通过。DeepSeek 第二轮超时缺 usage，
仅执行 2/34 轮后冻结，structured 未开始；共同门槛未达成，候选不切默认。
GLM 历史专项另计 1/2，不并入本套场景成功率。

[2026-10-19 批次](../reports/acceptance/2026-10-19-continuous-context-and-delivery.md)保留当时失败与费用，
不使用新结果改判，也不合成跨版本成功率。
历史快照的 62% 输入减少只属于[原实验](context-experiment.md)，不移植为本批降本结论。

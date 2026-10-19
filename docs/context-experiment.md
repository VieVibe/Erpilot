# 长对话与大工具响应上下文实验

本实验比较完整历史参考输入 `full`、现有截断策略 `current`、可选结构化视图 `structured`。正式默认策略不变。它分别记录输入信息保留、真实模型历史快照答题和临时库工作流验证，不把三类证据合成通用任务成功率。

## 冻结范围

十个固定场景覆盖短对话、24 轮背景、60 行大工具结果、48 轮与大结果组合、版本更新、多草稿、无草稿、最新读取失败、早期未持久化的独立约束，以及受保护系统内容超过预算。

压力预算为 4000 个启发式估算 token、最近 8 条消息、单工具正文 4000 字符；正式默认总预算仍为 24000。轮次中的背景文本是固定夹具，未声称这些背景轮次由模型实际生成。模型只回答最后一问，每个 case / 策略一次请求。

v1 商品编号与数量随行号递增；真实运行发现，被截断的第 31 行可以按规律猜中。因此 v1 只保留为诊断和用量记录，不作为大工具场景的信息保留证据。v1 的 DeepSeek 完整历史还存在复制限制时漏掉标签的严格失败，原判保留。v2 使用固定 SHA-256 派生的非连续 SKU 与数量，并在问题中明确要求复制标签和句号。两个版本的输入、判分与源码分别冻结，不拼接通过率。

## 可选视图

`agent_core.context_view.ContextView` 在请求前复制消息，保留系统消息、最近窗口、最近一次任务工具交换及对应用户原话；并行工具交换整体保留。应用层 `mcp_erp.context_view` 只将任务快照投影为客户、备注、版本、状态和按原顺序的 SKU / 数量。摘要仍是 tool 数据，并明确标记历史快照及必须重新查询。

无法结构化的大响应变为带哈希、原始长度和重新查询要求的合法 JSON，不截成半份政策或 JSON。最新工具失败也会保留，不用旧成功结果覆盖新失败。原始消息、工具内容和完整引用继续保存在 checkpoint；投影不创建批准、调用工具或变更 ERP。

应用代码可显式装配：

```python
from agent_core.runtime_config import LoopConfig
from mcp_erp.context_view import erp_context_view

config = LoopConfig(context_view=erp_context_view())
```

本批没有把候选策略设为 API / CLI 默认值，也没有建立跨会话记忆。用户早期独立表达但未写入任务状态的约束仍可能丢失；受保护内容可能超过预算。摘要省略价格和库存细节，查询这些字段时需要重新读取完整业务事实；候选策略不适合直接替换所有类型的上下文。

## 复现

离线只检查输入，零模型 / 嵌入请求；每次使用新的输出名：

```sh
uv run python -m evals.context_experiment --output reports/context/local-offline-01
uv run pytest packages/agent_core/tests/test_context_view.py packages/evals/tests/test_context_experiment.py
```

真实运行要求已授权的新批次预算和本地供应商配置；使用现有已确认费率，仅覆盖当前配置的端点，不自动切换端点或提供密钥：

```sh
uv run python -m evals.context_live --model glm-5.3-flash --budget 0.10 --budget-currency CNY --output reports/context/local-glm-01
uv run python -m evals.context_live --model deepseek-v4.1-flash --budget 0.05 --budget-currency USD --output reports/context/local-deepseek-01
```

SDK 重试为 0，单次总截止默认 60 秒。每次请求前写入并 fsync 日志，每次结束原子保存报告；输出名已存在即拒绝。没有自动续跑或重试失败；源代码变化中止批次，未知 usage 冻结后续请求。新请求只在已知费用小于预算时启动，估价熔断不是供应商账单硬上限。

## 如何读结果

- 离线 `retained` 是独立读取投影输入后与夹具金标比较；`estimated_input_tokens` 是 len/3 估算，`actual_usage` 与费用为空。
- 真实答题按固定六字段 JSON 检查，完整复制、类型和状态均须一致；它不使用另一个模型裁判。原文、usage、费用和禁止工具请求全部留存。
- 真实 token 来自端点 usage，延迟包含完整请求；只在同版本、同相关源码、同固定范围内比较策略。两模型结果分别报告。
- 临时库烟测使用脚本客户端读取实际投影，执行真实 LangGraph → FastMCP → SQLite：60 行草稿修改第 31 行，核对客户、备注、版本与全表状态。脚本结果不能替代真实模型操作成功率。
- 受保护内容超预算和未持久化约束丢失按失败或限制保留；候选策略的推广需要新的业务回归证据。

当前结果与费用汇总见[本批验收](../reports/acceptance/2026-10-19-context-and-portfolio.md)。

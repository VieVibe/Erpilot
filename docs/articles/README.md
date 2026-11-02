# Erpilot 开发实录：系列目录与写作计划

更新：2026-10-19。系列定位为技术学习分享，以代码、决策与真实验收为依据。
写作遵守 [writing-rules.md](writing-rules.md)；不把原计划的月份、周数当作实际工期。

## 站点核对

2026-10-19 经浏览器查看 [Vie 首篇文章](https://vie-vibe.cn/articles/ai/handwritten-agent-loop)：

- 系列名称为“Erpilot 开发实录”，导航显示 `part 1 of 1`。
- 首篇发布日期为 2026-09-30，结尾预告《给 ERP 写一个 MCP Server》。
- 已承接预告完成第 2–7 篇仓库文稿，并附 MCP 查询与幂等复现脚本；尚未导入站点或发布。

## 编排

| 顺序 | 标题 / 方向 | 要讲清的问题 | 状态与依据 |
|---|---|---|---|
| 01 | [手写 Agent Loop：从一次 API 调用到多步任务](01-handwritten-agent-loop.md) | 流式、工具调用、错误回填和 trace 如何组成循环 | 已发布；保留早期实现语境 |
| 02 | [给 ERP 写一个 MCP Server：工具契约、错误恢复与返回值设计](02-mcp-server.md) | 业务能力如何经过 MCP 接回 Agent；描述、错误、结果体积和桥接限制 | 文稿完成，待发布；[核对记录](reviews/02-mcp-server-review.md) |
| 03 | [让 Agent 等待审批：LangGraph 中断与固定版本确认](03-langgraph-approval.md) | 从回调门到 interrupt；确认对象、参数、版本与恢复后的决定如何绑定 | 文稿完成，待发布；[ADR-0006](../adr/0006-suspending-approval-events.md)、[ADR-0009](../adr/0009-langgraph-runtime.md)、[订单草稿设计](../superpowers/specs/2026-10-11-order-draft-design.md)、[业务审批设计](../superpowers/specs/2026-10-11-business-approval-design.md) |
| 04 | [工具超时以后，订单到底建没建：幂等与崩溃对账](04-idempotency-and-recovery.md) | 稳定 token、业务提交与 checkpoint 的窗口；展示记录为何不能决定重试 | 文稿完成，待发布；[ADR-0007](../adr/0007-write-reliability.md)、[ADR-0010](../adr/0010-crash-recovery-reconciliation-scope.md)、[崩溃恢复验收](../../reports/acceptance/2026-10-08-crash-recovery.md) |
| 05 | [怎样判断 Agent 做对了：从工具名到业务状态的评测](05-agent-evaluation.md) | 固定样本、判分版本、成功执行与零变更、预算、未知 usage 和跨模型对照 | 文稿完成，待发布；[标注标准](../eval-annotation-guide.md)、[质量门槛](../eval-quality-gates.md)、[评测叙事](../eval-regression-narrative.md) |
| 06 | [政策 RAG：检索命中之后，回答为什么仍然会错](06-rag-evidence-and-answers.md) | 冻结检索集、阈值和 BM25/RRF；逐问选证、本轮绑定、原文渲染与剩余失败 | 文稿完成，待发布；[检索实验](../retrieval-experiment.md)、[回答契约](../rag-answer-reliability.md)、[最新真实验收](../../reports/acceptance/2026-10-19-structured-policy-and-resume.md) |
| 07 | [对话裁短以后，业务任务还在吗：多轮状态与上下文实验](07-context-and-multi-turn-state.md) | 对话历史、持久化草稿和模型视图的区别；快照实验如何走到连续任务验证 | 文稿完成，待发布；[快照实验](../context-experiment.md)、[连续任务契约](../context-workflow.md)、[连续任务验收](../../reports/acceptance/2026-10-19-continuous-context-and-delivery.md) |

第 03–07 篇完整文稿及统一[核对记录](reviews/03-07-series-review.md)已完成，不预先承诺发布日期。
提示注入的评分器复判已纳入第 05 篇；攻击覆盖与边界另见[攻击面清单](../security/prompt-injection-attack-surface.md)，本批没有另增待写专题。

## 各篇范围

- 第 02 篇只讲基础工具链路；不在 MCP 教程里展开审批持久化和对账算法。
- 第 03 篇讲“确认了什么、如何等待并恢复”；第 04 篇讲“提交结果不明时如何查证与继续”。
- 第 05 篇保留旧失败和判分变更，定点重跑不拼成一次全量成功率。
- 第 06 篇分别讨论检索质量与回答质量。最新结构化问答尚未共同通过严格门槛，不能写成问题已全部解决。
- 第 07 篇区分固定压力样本与连续任务；候选视图仍为显式启用，不写成默认策略或两模型都已达标。

当前能力与限制以[项目状态](../project-status.md)为入口。阶段开发收口可以开始技术复盘，
文稿仍应保留未通过项、单进程范围和未部署等事实。

## 导入 Vie 时的约定

站点使用现有“Erpilot 开发实录”系列和 AI 工程分类。下表为导入建议，未写入站点元数据：

| 顺序 | 建议 slug | 摘要 | 标签 |
|---|---|---|---|
| 02 | `mcp-server-tool-contracts` | 把 ERP 查询接入 FastMCP，解释工具描述、业务错误、摘要与聚合返回，以及桥接中的校验和审批边界。 | Agent、MCP、Python |
| 03 | `langgraph-approval` | 从工具门到持久化中断，说明审批参数、草稿版本与业务预览如何固定，以及刷新后怎样找回原审批。 | Agent、LangGraph、HITL、Python |
| 04 | `idempotency-and-recovery` | 写工具超时后，按原 token 查询提交事实；结合事务幂等、SQLite 写锁和历史硬崩溃测试说明恢复边界。 | Agent、幂等、SQLite、Python |
| 05 | `agent-evaluation` | 从工具名和文本断言走向执行证据、业务状态与质量门槛，保留评分器修正、历史失败和未知计量。 | Agent、评测、Python |
| 06 | `rag-evidence-and-answers` | 分开检验政策检索与模型答复，比较向量、BM25 和 RRF，并说明本轮选证、程序原文渲染及未通过项。 | Agent、RAG、评测、Python |
| 07 | `context-and-multi-turn-state` | 区分任务状态、历史观察与请求视图，用固定快照和真实连续任务检验上下文投影、信息保留与保护参数。 | Agent、上下文工程、LangGraph、Python |

发布前按[撰写规则清单](writing-rules.md)复核，并确认：正文的草稿标记与日期、源码链接对应版本、
复现脚本已随代码推送、仓库相对链接改为站点路由或公开源码链接、站点文章目录及系列排序、前后篇链接，以及站点构建和阅读预览。

# 项目当前状态

更新：2026-10-19。本页是当前能力和范围的入口；日期更早的计划与验收保留其当时结论。

| 项目 | 当前状态 | 证据 |
|---|---|---|
| B1–B3：计价、草稿与业务审批 | 已实现版本审批、幂等与事务复核 | [业务路线](business-roadmap.md)、[三分钟演示](demo-guide.md) |
| B4：多轮任务与指代 | 最终专项两模型各 3/3 场景、7/7 轮，gate / strict 通过 | [最终专项](../reports/acceptance/2026-10-19-b4-final-validation.md)；旧 B4 十五轮结果不改判 |
| B5：政策与业务判断 | 原七例专项两模型各 7/7；最新独立结构化问答两模型各 11/13，严格门槛未达成 | [最新回答验收](../reports/acceptance/2026-10-19-structured-policy-and-resume.md) |
| B6：只读补货建议 | 最终同源码两模型各 7/7，gate / strict 通过 | [最终验收](../reports/acceptance/2026-10-19-live-model-validation.md) |
| 检索实验 | 完整真实查询向量对照；RRF 未超过当前策略，保留默认阈值 | [检索实验](retrieval-experiment.md) |
| 长历史 / 大响应 | 可选视图真实答题 GLM 8/10、DeepSeek 9/10；现有策略各 2/10。固定压力样本，候选未全面通过 | [实验契约](context-experiment.md)、[本批验收](../reports/acceptance/2026-10-19-context-and-portfolio.md) |
| 求职展示 | 本地正式 API 演示、字幕录屏、三个案例、可校验源码快照 | [作品入口](portfolio-overview.md)、[讲解材料](interview-cases.md) |

每组真实结果是对应冻结用例、源码和预算下的一次观察，不合成跨版本统一成功率。旧未知 usage 与冻结预算保持独立，用户已跳过的供应商账单核销不重新追索。

## 当前开发顺序

用户本轮选择推进上下文工程和作品整理。上下文候选保持可选，完整历史仍作为展示与恢复依据；推广前需补针对实时业务任务、早期独立约束和预算溢出的回归。政策结束协议与条件拆分保留为独立待办，本批不将 11/13 改为完成。

维持本机可信单用户、单 API 进程范围。本阶段没有部署，身份认证、租户隔离与多 worker 执行权需要独立设计。崩溃恢复历史硬退出与组件证据见[原报告](../reports/acceptance/2026-10-08-crash-recovery.md)，ADR-0010 仍待评审。

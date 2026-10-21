# 项目当前状态

更新：2026-10-21。本页是当前能力和范围的入口；日期更早的计划与验收保留其当时结论。

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 预算真实模型专项 | GLM 1/1 完整流程、10/10 受控阶段；16 请求 / 16 usage，估价 CNY 0.015244；客户缩写拦截纠正、超额实查停止、明确提高预算、恢复审批与最终事实通过。单批固定范围，DeepSeek 跳过 | [真实验收](../reports/acceptance/2026-10-21-budget-live-validation.md) |
| 订单预算与待确认事项 | 人民币预算随版本保存；数量 / 报价变化后检查，超额显示差额并拦截提交；明确调整后重新核对审批。Python 1206 / 21 skipped、前端 93、UI 31、正式本地演示 4 项通过，本批未调用付费模型 | [功能契约](order-budget.md)、[本批验收](../reports/acceptance/2026-10-21-order-budget.md) |
| 跨模型整单真实验收：独立新批次 | GLM 3/3、28/28 阶段通过；DeepSeek 上游 502 缺 usage，用户已跳过后续验收，不再阻塞开发；共同门槛未达成 | [本批结果与计量](../reports/acceptance/2026-10-21-cross-model-order-acceptance.md) |
| 指定版本核对与直接申请审批 | 网页核对完整版本后直接进入正式 HITL；无模型请求，冲突不自动改版；批准仍复核业务条件。Python 1166 / 21 skipped，前端 91、UI 29、本地正式服务演示 3 项通过 | [功能契约](draft-confirmation.md)、[本批验收](../reports/acceptance/2026-10-21-draft-confirmation.md) |
| 完整订单任务验收：前批 | 三流程 / 28 阶段、全表与答复核对；提交受阻后程序终止。前批 GLM 原流程 2/3，明确确认外部变更的独立流程 11/11 阶段；DeepSeek 限流缺 usage 冻结，旧结果保留 | [功能与范围](whole-order-acceptance.md)、[前批验收](../reports/acceptance/2026-10-21-whole-order-acceptance.md) |
| 跨轮约束与修改记录 | 来源与实际变更随版本保存；网页分页。明确 ID 历史查询先实查再请求模型；最终源码 GLM 历史 2/2，DeepSeek 中断未完整验证 | [功能与边界](draft-history.md)、[最新验收](../reports/acceptance/2026-10-21-whole-order-acceptance.md)、[工程验收](../reports/acceptance/2026-10-21-draft-history.md) |
| 多轮订单可靠性：参数来源 | 本批实际拦截一次客户缩写和一次未读草稿修改；15 个新版本的字段来源及变更一致。格式与语义范围仍有限 | [实现范围](order-task-reliability.md)、[真实验收](../reports/acceptance/2026-10-21-live-order-validation.md) |
| B1–B3：计价、草稿与业务审批 | 已实现版本审批、幂等与事务复核 | [业务路线](business-roadmap.md)、[三分钟演示](demo-guide.md) |
| B4：多轮任务与指代 | 最终专项两模型各 3/3 场景、7/7 轮，gate / strict 通过 | [最终专项](../reports/acceptance/2026-10-19-b4-final-validation.md)；旧 B4 十五轮结果不改判 |
| B5：政策与业务判断 | 原七例专项两模型各 7/7；最新独立结构化问答两模型各 11/13，严格门槛未达成 | [最新回答验收](../reports/acceptance/2026-10-19-structured-policy-and-resume.md) |
| B6：只读补货建议 | 最终同源码两模型各 7/7，gate / strict 通过 | [最终验收](../reports/acceptance/2026-10-19-live-model-validation.md) |
| 检索实验 | 完整真实查询向量对照；RRF 未超过当前策略，保留默认阈值 | [检索实验](retrieval-experiment.md) |
| 长历史 / 大响应 | 可选视图真实答题 GLM 8/10、DeepSeek 9/10；现有策略各 2/10。固定压力样本，候选未全面通过 | [实验契约](context-experiment.md)、[本批验收](../reports/acceptance/2026-10-19-context-and-portfolio.md) |
| 连续业务上下文 | 10-21 新批次 GLM structured 3/3 场景、17/17 轮，current 1/3、13/15 执行轮通过；DeepSeek 2/34 轮后因超时缺 usage 冻结，共同门槛未达成，候选继续可选 | [连续任务契约](context-workflow.md)、[最新验收](../reports/acceptance/2026-10-21-live-order-validation.md)；[10-19 结果](../reports/acceptance/2026-10-19-continuous-context-and-delivery.md)独立保留 |
| 求职展示 | 统一投递材料、已有字幕录屏、三个技术取舍、带内外层校验清单的交付包 | [投递材料](application-kit.md)、[作品入口](portfolio-overview.md)、[讲解材料](interview-cases.md) |

每组真实结果是对应冻结用例、源码和预算下的一次观察，不合成跨版本统一成功率。旧未知 usage 与冻结预算保持独立，用户已跳过的供应商账单核销不重新追索。

## 当前开发顺序

按用户本次指令已新建独立跨模型批次，固定原三流程，不改确认文字或金标。
GLM 本批 3/3、28/28 阶段全部通过；DeepSeek 首次请求上游 502 缺 usage 后停止，
后续阶段未执行。用户随后判断可能是中转上游波动，明确决定跳过该项；不再重试，
也不把它作为后续开发前置。新旧未知费用和共同门槛未达成的事实保留；详见[最新真实记录](../reports/acceptance/2026-10-21-cross-model-order-acceptance.md)。

用户选择的连续业务上下文验收与投递材料已交付。新候选保留用户原话、明确行号和报价快照金额，
受保护消息超过估算预算时在请求前停止；真实两模型同源码对照尚未共同达标，候选保持可选。
2026-10-21 已按用户确认的可靠订单主线新增有限字面参数检查：识别到的客户缩写会在保存前拒绝，
并保留已有草稿客户与备注；后续已完成[来源持久化与修改记录](draft-history.md)，刷新页面或恢复旧任务
仍可核对原要求。后续已补明确历史目标的前置实查、数量 SKU 必填及后端行数，
并建立[完整任务验收](whole-order-acceptance.md)。提交复核受阻后程序结束本轮，防止自行改稿。
GLM 历史 2/2；原完整流程因笼统确认后的额外澄清保留 2/3，明确接受外部变更的独立流程通过。
后续已新增[指定版本核对入口](draft-confirmation.md)：用户在页面核对完整报价后直接申请正式审批，
无需模型解释确认文字，成功结果由实际订单生成；手机端逐项核对，版本冲突停止。该入口工程验收通过，
不改判原自然语言三流程 2/3，也不代表跨模型共同达标。
此前 DeepSeek 限流缺 usage 的历史冻结保留；本次补验收上游 502 后已按用户决定跳过，
共同完整验收及重复验证不再作为当前开发前置。
后续已完成[预算上限与预算超额待确认事项](order-budget.md)，预算原话 / 表单来源随版本保存，
超额在确认接口、模型审批前及实际建单事务受阻；明确调整后需要新的核对与审批。
更广的未决问题、交期及商品替代约束仍待设计。政策结束协议与条件拆分仍为独立待办，
本批政策问答结果继续保持 11/13。

预算工程之后已按用户要求完成[GLM 真实专项](../reports/acceptance/2026-10-21-budget-live-validation.md)：
1/1 完整流程、10/10 受控阶段；16 请求计量完整，一次客户名缩写在保存前被拒绝并纠正。
正式功能源码未改，单批观察不推广为所有表达或两模型共同稳定率。

维持本机可信单用户、单 API 进程范围。本阶段没有部署，身份认证、租户隔离与多 worker 执行权需要独立设计。崩溃恢复历史硬退出与组件证据见[原报告](../reports/acceptance/2026-10-08-crash-recovery.md)，ADR-0010 仍待评审。

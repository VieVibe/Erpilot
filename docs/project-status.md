# 项目当前状态

更新：2026-11-04。本页是当前能力和范围的入口；日期更早的计划与验收保留其当时结论。

## 当前交付

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 最终打磨与可展示交付 | 输入边界、加载锁定、初次会话空态、录屏确认与报告图片补齐；Python **1861 / 0 skipped**、前端116、UI60/2实验跳过、正式离线浏览器5、容器3、场景11/11；干净源码复现及浏览器零错误/告警、依赖审计零已知漏洞，新字幕录屏与交付包 | [本轮清单与验收](../reports/acceptance/2026-11-04-finishing.md)、[作品入口](portfolio-overview.md) |
| 标签委托审核 | **120/120已审核**，6条补充范围说明，判定/字段改动0；无用户待审项。审核后规则120/120，评测专项449通过、无付费调用 | [审核说明](label-review.md)、[逐条记录](../reports/review/20261104-intent-label-audit/review.md)、[专项JUnit](../reports/acceptance/20261104-intent-label-review-evals.xml) |
| 语义默认启用收尾 | 真实 API 默认创建/修改预览；完整 HTTP **8/8**，该轮全仓 PG17 **1817 passed**、前端112、UI58、离线浏览器5、容器3、场景11/11；随后120条标签委托AI审核完成 | [默认启用收尾](../reports/acceptance/2026-11-04-semantic-default.md)、[当前标签审核](label-review.md) |
| 最新真实验收 | 订单同批 **4/4 / 34/34**、55 请求/usage；创建 **7/7**、修改 **6/6**；默认上下文的带登录真实 HTTP 六项检查通过。模型/字段失败与未知费用保留 | [最终验收](../reports/acceptance/2026-11-04-real-model-reconnect.md)、[独立审计](../reports/acceptance/20261104-reconnect-final-audit.json) |
| 本轮修复与回归 | 当前查询不再误标历史；明确备注字段作用域、完整原文和动作来源结构；全仓 PG17 **1796 passed**，随后 HTTP/SSE 与语义协议专项 **19 passed** | [自审](../reports/review/20261104-real-model-reconnect.md)、[验收](../reports/acceptance/2026-11-04-real-model-reconnect.md) |
| 本轮最终工程验收 | Python 1783 / 0 skipped / 47 live eval deselected；前端 112、UI 57 / 2 skipped、正式离线 API 浏览器 5、容器浏览器 3、场景 11/11；实查依赖故障及 live 配置启动 | [本批验收](../reports/acceptance/2026-11-04-production-readiness.md) |
| 同源容器与持久恢复 | 前端/API 镜像 + PostgreSQL，非 root / 根只读、健康检查、业务与 checkpoint 持久卷；默认无密钥，真实模型显式启用 | [部署指南](deployment.md)、[本批验收](../reports/acceptance/2026-11-04-production-readiness.md) |
| 最小身份与权限 | 本地账号、HttpOnly cookie / CSRF、服务端会话归属、只读/审批角色及操作者审计；共享门店，不能跨账号查任务或代批 | [ADR-0012](adr/0012-authenticated-container-deployment.md)、[本批验收](../reports/acceptance/2026-11-04-production-readiness.md) |
| 前一批真实订单 v2（保留） | 执行 7 / 计划 34 阶段，通过 6；完整任务 0/4。11 请求、10 usage，估价 CNY 0.009714 + 1 笔未知；当时连接失败后冻结，现由新授权重连批次继续验收 | [独立旧报告](../reports/acceptance/20261104-readiness-order-v2.json)、[当时说明](../reports/acceptance/2026-11-04-production-readiness.md) |
| 一键运行与统一验收 | 环境诊断、冻结安装、构建、无密钥演示与带日志验收；前一阶段 Python 1771、前端 106，已完成源码副本从零安装和 320/1440px 验证；本轮结果见首行 | [前一阶段验收](../reports/acceptance/2026-11-04-portfolio-maturity.md)、[开发指南](development.md) |
| 求职展示入口 | README 按业务场景、启动、架构、取舍和证据组织；补实际架构及当前技术选择，旧规划不冒充实现 | [实际架构](architecture.md)、[交付决策](adr/0011-local-portfolio-delivery.md) |
| 意图规则、澄清与完整预览 | 已支持候选编号、多轮补件数、字段原文、原子消费、完整表单、来源与会话隔离；11-02 API 诊断和压力验证已留证 | [完整范围](intent-analysis.md)、[11-02 工程证据](../reports/acceptance/2026-11-02-intent-diagnostics.md) |
| 语义创建 / 修改候选 | 单次候选、来源/目录/字段作用域验证、完整预览、独立原币种预算；修改绑定具体版本。默认启用；120条委托AI审核完成，规则回归120/120，无用户待审项 | [使用指南](semantic-parsing.md)、[当前标签审核](label-review.md) |
| 模式回退与恢复 | off / shadow 回退使未消费请求 needs_recheck；旧审批与已保存新来源可恢复。11-02 历史全仓真实 PG 1764 passed，不视为本次复测 | [11-02 恢复验收](../reports/acceptance/2026-11-02-intent-mode-recovery.md) |
| 演示与证据保护 | 政策脚本接入本轮结构化选证；浏览器正文有依据时不再降级。截图改为逐测试独立路径，旧历史图片恢复原样，复测及 CI 防止覆盖 | [修复和验证](../reports/acceptance/2026-11-04-portfolio-maturity.md)、[结构化审查](../reports/review/20261104-portfolio-maturity.md) |

默认核心链路继续为订单草稿 → 指定版本核对 → 独立审批 → 事务复核及建单，
加政策证据与只读补货。按用户最新要求，语义预览完成收尾并作为真实 API 默认能力，见[配置与边界](semantic-parsing.md)。

## 历史能力与独立真实观察（保留原批次）

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 自然商品输入 | 完整商品名称 / SKU 与规范中文件数核对；重名、简称与单位不明先澄清；保存时重查目录，来源随版本保存。当前草稿支持唯一商品名 / 行号及中文件数。Python 1446 / 21 skipped、前端 97、UI 44、正式离线浏览器 5、演示 11/11，无供应商请求 | [使用与范围](natural-order-entry.md)、[本批验收](../reports/acceptance/2026-10-28-natural-order.md) |
| 政策答复 v4 | 检索受阻由程序结束；条件留在所属问题。新 GLM 13/13、22 请求 / usage，估价 CNY 0.015908；受控检索、零嵌入，旧 v3 两模型各 11/13 保留 | [实现、验证与边界](../reports/acceptance/2026-10-28-contracts-and-policy.md) |
| 订单验收契约 v2 | 超预算只读停止与直接提交拦截分开；审批请求明确本轮实查。离线四流程通过；真实前三阶段通过，第四阶段连接错误缺 usage 后冻结，整单未完成 | [独立新批次](../reports/acceptance/2026-10-28-contracts-and-policy.md) |
| P0 / P1 独立 GLM 真实验收 | 四条计划流程两条完整通过；计划 34 阶段，执行 25、通过 23，9 阶段未执行；预算提前停止与同轮读取要求存在两处失败。38 请求 / 38 usage，估价 CNY 0.035089，原门槛未通过；DeepSeek 继续跳过 | [本批结果与离线复核](../reports/acceptance/2026-10-28-order-reliability-live.md) |
| P0 / P1 订单可靠性 | 首次创建完整商品明细核验与来源、动态待确认事项、审批前 / 事务复核；Python 1368 / 21 skipped、前端 96、UI 42、正式离线服务 4 项、新任务集离线 4/4 流程 / 34/34 阶段；零供应商请求 | [本批功能与范围](order-reliability-p0-p1.md)、[工程验收](../reports/acceptance/2026-10-27-order-reliability.md) |
| 草稿商品变更核验 | 已有草稿明确数量与目标匹配；未指定商品保持，明确追加仅限末行；依据随版本保存。Python 1317 / 21 skipped、新增专项 108、前端 93、离线演示 10/10；无付费调用 | [功能契约](draft-item-constraints.md)、[本批验收](../reports/acceptance/2026-10-22-draft-item-guard.md) |
| 预算真实模型专项 | GLM 1/1 完整流程、10/10 受控阶段；16 请求 / 16 usage，估价 CNY 0.015244；客户缩写拦截纠正、超额实查停止、明确提高预算、恢复审批与最终事实通过。单批固定范围，DeepSeek 跳过 | [真实验收](../reports/acceptance/2026-10-21-budget-live-validation.md) |
| 订单预算与待确认事项 | 人民币预算随版本保存；数量 / 报价变化后检查，超额显示差额并拦截提交；明确调整后重新核对审批。Python 1206 / 21 skipped、前端 93、UI 31、正式本地演示 4 项通过，本批未调用付费模型 | [功能契约](order-budget.md)、[本批验收](../reports/acceptance/2026-10-21-order-budget.md) |
| 跨模型整单真实验收：独立新批次 | GLM 3/3、28/28 阶段通过；DeepSeek 上游 502 缺 usage，用户已跳过后续验收，不再阻塞开发；共同门槛未达成 | [本批结果与计量](../reports/acceptance/2026-10-21-cross-model-order-acceptance.md) |
| 指定版本核对与直接申请审批 | 网页核对完整版本后直接进入正式 HITL；无模型请求，冲突不自动改版；批准仍复核业务条件。Python 1166 / 21 skipped，前端 91、UI 29、本地正式服务演示 3 项通过 | [功能契约](draft-confirmation.md)、[本批验收](../reports/acceptance/2026-10-21-draft-confirmation.md) |
| 完整订单任务验收：前批 | 三流程 / 28 阶段、全表与答复核对；提交受阻后程序终止。前批 GLM 原流程 2/3，明确确认外部变更的独立流程 11/11 阶段；DeepSeek 限流缺 usage 冻结，旧结果保留 | [功能与范围](whole-order-acceptance.md)、[前批验收](../reports/acceptance/2026-10-21-whole-order-acceptance.md) |
| 跨轮约束与修改记录 | 来源与实际变更随版本保存；网页分页。明确 ID 历史查询先实查再请求模型；最终源码 GLM 历史 2/2，DeepSeek 中断未完整验证 | [功能与边界](draft-history.md)、[最新验收](../reports/acceptance/2026-10-21-whole-order-acceptance.md)、[工程验收](../reports/acceptance/2026-10-21-draft-history.md) |
| 多轮订单可靠性：参数来源 | 本批实际拦截一次客户缩写和一次未读草稿修改；15 个新版本的字段来源及变更一致。格式与语义范围仍有限 | [实现范围](order-task-reliability.md)、[真实验收](../reports/acceptance/2026-10-21-live-order-validation.md) |
| B1–B3：计价、草稿与业务审批 | 已实现版本审批、幂等与事务复核 | [业务路线](business-roadmap.md)、[三分钟演示](demo-guide.md) |
| B4：多轮任务与指代 | 最终专项两模型各 3/3 场景、7/7 轮，gate / strict 通过 | [最终专项](../reports/acceptance/2026-10-19-b4-final-validation.md)；旧 B4 十五轮结果不改判 |
| B5：政策与业务判断 | 原七例两模型各 7/7；旧 v3 两模型各 11/13；新 v4 GLM 独立 13/13，DeepSeek 未运行 | [新回答验收](../reports/acceptance/2026-10-28-contracts-and-policy.md)、[旧结果](../reports/acceptance/2026-10-19-structured-policy-and-resume.md) |
| B6：只读补货建议 | 最终同源码两模型各 7/7，gate / strict 通过 | [最终验收](../reports/acceptance/2026-10-19-live-model-validation.md) |
| 检索实验 | 完整真实查询向量对照；RRF 未超过当前策略，保留默认阈值 | [检索实验](retrieval-experiment.md) |
| 长历史 / 大响应 | 可选视图真实答题 GLM 8/10、DeepSeek 9/10；现有策略各 2/10。固定压力样本，候选未全面通过 | [实验契约](context-experiment.md)、[本批验收](../reports/acceptance/2026-10-19-context-and-portfolio.md) |
| 连续业务上下文 | 10-21 新批次 GLM structured 3/3 场景、17/17 轮，current 1/3、13/15 执行轮通过；DeepSeek 2/34 轮后因超时缺 usage 冻结，共同门槛未达成，候选继续可选 | [连续任务契约](context-workflow.md)、[最新验收](../reports/acceptance/2026-10-21-live-order-validation.md)；[10-19 结果](../reports/acceptance/2026-10-19-continuous-context-and-delivery.md)独立保留 |
| 求职展示 | 统一投递材料、已有字幕录屏、三个技术取舍、带内外层校验清单的交付包 | [投递材料](application-kit.md)、[作品入口](portfolio-overview.md)、[讲解材料](interview-cases.md) |

每组真实结果是对应冻结用例、源码和预算下的一次观察，不合成跨版本统一成功率。旧未知 usage 与冻结预算保持独立，用户已跳过的供应商账单核销不重新追索。

## 当前开发顺序

2026-11-04：基础部署/身份后，用户明确允许连接中断继续尝试。按[重连计划](plans/2026-11-04-real-model-reconnect.md)
完成可恢复验收，修复真实暴露的上下文/语义问题。默认核心完整真实验证已通过；
本段保留该批次结论；随后默认启用受约束预览，再按用户委托完成120条AI标签审核，见[当前记录](label-review.md)。
以下为历史开发顺序，各条“后续”均指其当时状态，最新状态以上方当前交付为准。

2026-10-28（自然输入）：按用户选择完成商品名称与中文件数路径。只读解析当前完整要求，商品名称精确且唯一才转为 SKU；候选需明确选择并重述本轮完整要求，包括要保留的备注与预算。保存事务重新核对来源、目录和全部件数；旧草稿修改保留其已保存约束。新功能完成离线、API 与浏览器验收，没有新增真实模型请求，不改判旧政策结果，不重开订单冻结批次。详见[自然输入验收](../reports/acceptance/2026-10-28-natural-order.md)。

2026-10-28 后续已完成订单 v2 契约与政策程序结束修复。Python 1387 passed / 21 skipped、前端 96、正式离线浏览器 4、演示 10/10。新政策 v4 GLM 13/13；订单 v2 因连接中断缺 usage 停止，不自动重试，不与政策结果混算。新增已知估价 CNY 0.022683，另有 1 请求未知费用。流清理警告经 100 次离线探针未复现，仍未归因；旧结果和冻结保留。详见[本批报告](../reports/acceptance/2026-10-28-contracts-and-policy.md)。

以下保留前批的开发顺序及当时结论，后续完成项以上述新批次为准。

2026-10-28 已完成 P0 / P1 独立 GLM 真实观察与离线复核，原冻结门槛未通过：2/4 完整流程，23/34 计划阶段通过（执行 25）。预算超额后实际查询并提前停止，但原评分要求提交尝试；价格刷新后直接申请新版审批，未满足该阶段同轮读取事项要求。原结果不回填、不重试；后续先明确两处验收契约，并检查控制台流清理警告，再安排独立版本验证。政策结束协议及条件拆分仍为后续开发项，11/13 旧结果不变。

2026-10-27 按用户要求实施 P0 / P1：[创建明细核验与待确认事项](order-reliability-p0-p1.md)。
本轮明确商品清单与首次创建参数核对；不支持或有歧义的要求先澄清，错误明细不落库。
读取时统一计算预算、有效期、版本、商品与库存事项；多草稿先选目标，受阻时不进入审批。
新增独立完整任务集，不修改旧固定用例与真实模型判定；无付费调用。

2026-10-22 继续可靠订单主线，补上[已有草稿的商品变更核验](draft-item-constraints.md)：
数量与当前用户明确目标匹配；刷新报价或修改客户、备注、预算不能夹带改行。
保留原预算流程的明确追加商品，检查证据随版本保存；创建商品语义和通用自然语言仍不作保证。
工程测试使用脚本模型与临时库，没有重开已跳过的 DeepSeek 调用或改判历史真实结果。

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

以上为历史批次范围。当前已有带账号的同源容器和任务归属隔离，仍为共享门店、单 API 进程；
租户隔离和多 worker 执行权未交付。崩溃恢复历史硬退出与组件证据见[原报告](../reports/acceptance/2026-10-08-crash-recovery.md)，ADR-0010 仍待评审。

# Erpilot

> 会请示的 ERP 智能体（an approval-aware ERP agent）—— agent 应用开发求职作品集项目（2026-09 启动，工期 12 个月）

**一句话**：运行在 mini-ERP（商品 / 库存 / 订单）上的业务智能体：FastMCP 工具层 + 分级人工审批（HITL）+ 全程评测驱动；前端提供流式对话、工具时间线与审批卡片。

**求职演示入口**：[三分钟本地演示指南](docs/demo-guide.md)。无需模型密钥或 PostgreSQL，使用临时数据库展示草稿修改 → 指定版本审批 → 建单，以及政策证据、无依据拒答和补货建议。脚本化模型与关键词向量仅用于流程演示；真实模型结果见下方独立验收记录。

## 为什么叫 Erpilot

ERP + (co)pilot。产品隐喻是一位**掌柜**：掌柜打理店铺日常——查库存、对订单、算报价，样样精通；但**动账动货的事，必须请示东家**。

这正是本项目的核心设计：只读操作自动执行，低风险操作批量确认，资金相关操作单笔审批。

## 技术栈

当前实现：Python 3.12 · LangGraph · FastAPI · FastMCP · SQLite / SQLAlchemy ·
PostgreSQL checkpoint · OpenAI 兼容 SDK · GLM-5.3 Flash · 本地 JSONL（可选 Langfuse sink）· React + TypeScript。
ERP 业务数据与 API RunStore 仍在 SQLite；PostgreSQL 只保存图状态。

选型理由见 [`docs/adr/0001-tech-stack.md`](docs/adr/0001-tech-stack.md)。

## 仓库结构

```
├── apps/
│   ├── api/            # FastAPI：agent 宿主、会话管理、SSE
│   ├── cli/            # erpilot CLI：typer + rich（组合层，默认 MCP 真数据）
│   └── web/            # React + TS：流式对话、工具时间线、审批卡片（M1 第 4 周初始化）
├── packages/
│   ├── agent_core/     # LangGraph 运行时（不依赖任何业务包）
│   ├── mcp_erp/        # FastMCP Server：ERP 能力 → MCP 工具 + agent 桥
│   ├── erp_store/      # 领域模型 + 种子数据（商品/库存/订单，SQLite）
│   └── evals/          # 评测集 + runner + 报告
└── docs/adr/           # 架构决策记录
```

## 快速开始

```bash
# 安装 uv（若未安装）：https://docs.astral.sh/uv/
uv sync --all-packages       # 创建虚拟环境并安装全部工作区依赖
cp .env.example .env         # 填供应商 key，或 LLM_API_KEY + LLM_BASE_URL + LLM_MODEL
docker compose up -d postgres # API graph checkpoint（本地演示凭据仅供开发）
uv run pytest                # 单元测试（mock，不消耗 token）

# CLI：默认经 MCP 桥查询真数据（先 seed），trace 自动落盘 traces/*.jsonl
uv run --package erp-store python -m erp_store seed
uv run erpilot chat "订单 123 里买了什么？还有货吗？有货的话报个价"
uv run erpilot chat --tools demo                # 假 ERP 工具，仍调用真实模型
uv run erpilot chat --writes "给商品 A1001 入库 5 件"  # 终端审批后写入
uv run erpilot replay traces/<某个>.jsonl        # 把 trace 还原成可读对话

# API + 前端：SSE 链路
uv run --package erpilot-api python -m erpilot_api
# 启用审批写入时再设置 ERPILOT_WRITES=1；缺少/无法连接 checkpoint DB 时 API 会拒绝启动
# .env 设置 ERPILOT_TOOLS=mcp 连接真实 ERP，ERPILOT_THINKING=1 显示供应商思考增量。
# 页面顶部显示数据源及写入能力；切换工具模式后使用“新会话”，避免混用旧历史。
# 验证：http://127.0.0.1:8000/healthz
cd apps/web && npm install && npm run dev      # http://localhost:5173

# MCP server 独立进程（stdio，给外部 MCP 客户端）
uv run --package mcp-erp python -m mcp_erp serve

# 统一真实模型基线：29 读 + 3 写拒绝 + 3 脚本化审批 = 35 条（消耗 token）
uv run python -m evals --budget 0.1 --timeout 30
uv run python -m evals --budget 0.02 --case app-01 --case app-02  # 定点报告
# 报告落 reports/evals/；标注标准见 docs/eval-annotation-guide.md
```

### 无密钥演示与写入可靠性

```bash
uv run python scripts/demo.py                  # 十个离线场景，逐项断言并保存 trace
uv run python scripts/demo.py --scenario order --scenario replenishment
# 浏览器演示：先在 apps/web 中 npm ci && npm run build，再回到仓库根目录
uv run python scripts/demo.py --serve --port 8765
# 打开 http://127.0.0.1:8765；使用页面演示按钮填入指令并发送
# 草稿 → 修改第二行 → 提交草稿 → 核对版本后批准/拒绝；另有政策证据与补货建议
```

该演示使用脚本化模型和关键词演示向量，执行正式 API → LangGraph → MCP → 审批 → 临时 SQLite；
不消耗模型 token，不改已有业务与会话库，也不代表真实模型成功率或嵌入质量。trace 与逐项断言结果落
`traces/demo/`，浏览器 trace 落 `traces/browser-demo/`。刷新页面可恢复当前进程内的历史与审批；重启后清空。
`npm --prefix apps/web run test:demo` 对构建产物和真实离线 API 运行浏览器验收。完整命令与讲解顺序见[演示指南](docs/demo-guide.md)。

四个写工具支持 `client_token`：同键同参数返回原结果，同键不同参数报
`idempotency_conflict`；bridge 自动为单次调用生成并复用 token。业务变更与
幂等记录同事务提交，SQLite 写事务在读取库存之前取得写锁，避免并发超卖。
写工具自动重试只在具备幂等契约时启用；整轮写任务不自动重跑。

审批通过 LangGraph `interrupt` 挂起，API 使用 PostgreSQL checkpoint 保存待审批
参数与稳定 token；页面断连后可读取 session snapshot 并通过恢复 SSE 提交决策。
节点执行中断后也可从保存的 checkpoint 显式继续，历史工具成功/失败状态会保留。
ERP 业务数据和 RunStore 仍保存在 SQLite。此版本不实现 ADR-0008 中完整的
R01–R10 崩溃对账与自动续跑协议，也未加入身份认证和多进程调度；边界见
[ADR-0009](docs/adr/0009-langgraph-runtime.md)、[ADR-0008](docs/adr/0008-persistent-run-recovery.md)
与[可靠性复盘](docs/write-reliability.md)。

Langfuse 双写（可选）：`.env` 配置 `LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
LANGFUSE_HOST` 后，CLI / API / 评测的 trace 在本地 JSONL 兜底之外同步上报远程
（需 `uv sync --package agent-core --extra langfuse`）；未配置时零成本纯本地。

M1 各周验收入口：第 1–3 周为 `python -m agent_core`（最简演示，多步任务 + 并行工具）；
第 4 周为上述 `erpilot` CLI 与 API/前端链路。

## 下一阶段规划

B6 已实现第一版只读补货建议。设置 `ERPILOT_TOOLS=mcp` 后可问「按最近 30 天销量、采购周期 7 天、到货后覆盖 14 天，哪些商品需要补货？」；工具按当前库存和有效订单件数计算目标量、建议量及预计可售天数，网页展示统计窗口、假设和限制。采购周期未知时明确未计入采购等待需求；零销量、下架及异常库存不伪造建议数量。不会采购或调整库存。独立 `--suite b6` 七场景已接入固定时点/临时库、全表零变更和原币种预算；此次仅完成离线工程验收，真实模型表现仍待单独验证。见 [B6 设计](docs/superpowers/specs/2026-10-17-replenishment-design.md)与[验收记录](reports/acceptance/2026-10-17-replenishment.md)。B4 真实复测及未知用量预算继续独立保留。

功能深化路线见 [业务里程碑与后续路径](docs/business-roadmap.md)：计价一致 → 订单草稿与审批闭环 → 业务审批摘要 → 多轮任务与历史会话 → 业务 RAG → 补货建议。本阶段暂不考虑部署。B1 计价一致已完成；B2 支持会话草稿、多商品编辑和固定版本审批，提交时重新校验报价与库存，同一草稿重复提交只建一单。实现和验收入口见 [B2 订单草稿计划](docs/superpowers/plans/2026-10-11-order-draft.md)。

使用订单草稿页面需设置 `ERPILOT_TOOLS=mcp`；保存草稿不扣库存。`ERPILOT_WRITES=true` 且审批启用时才可提交建单。草稿每版有效 30 分钟，价格或库存变化后需刷新并重新确认；跨会话草稿不可读取或提交。直接调用独立 MCP 的原始 `create_order` 仍按执行时现价建单，审批由调用方负责。

B3 取消订单与库存调整先保存业务预览再审批：确认页展示客户、原单金额、状态及库存变化，条件变化会停止执行并提示重新确认；同一预览重复提交只生效一次。取消不执行退款，缺失库存行会明确提示无法回补。设计与开发入口见 [B3 业务审批计划](docs/superpowers/plans/2026-10-11-business-approval.md)。

B4 支持从持久化草稿继续多轮任务：单份未提交草稿可找回客户、商品、数量与备注，多份先澄清；按指定版本的商品行修改数量，不依赖旧聊天文本。网页“历史会话”可分页找回旧任务，切换不会执行或取消待审批操作，恢复失败时保持输入锁定。见 [B4 验收](reports/acceptance/2026-10-11-task-history.md)与[实施计划](docs/superpowers/plans/2026-10-11-task-history.md)。

B4 新增独立多轮模型评测入口：`uv run python -m evals --suite b4 --budget 0.05 --budget-currency CNY --timeout 60`。七个场景共十五轮覆盖行号指代、连续修改、多草稿澄清、指定目标、丢失聊天后的恢复、旧版本商品顺序及会话隔离；报告逐轮记录可见原文、工具、草稿状态和 trace。2026-10-17 新批次 **GLM 7/7、DeepSeek 6/7，均十五轮完整执行**，同源码 strict 对照通过；DeepSeek 在商品顺序变化后先澄清而未修改，原门槛未通过。旧 GLM 超时已完成本地计量审计，用户补充费用 USD 0.000222，原缺失 usage 保留。见[本轮计量与对照验收](reports/acceptance/2026-10-17-b4-model-comparison.md)；[2026-10-16 原始失败与 DeepSeek 7/7 记录](reports/acceptance/2026-10-16-task-model-evaluation.md)不合并成功率。DeepSeek 运行须使用 USD 预算。

2026-10-17 草稿增量区分明确当前行号与旧展示歧义，数量修改可绑定 `expected_sku`；草稿响应及页面明确库存是报价时预计值，保存不预留、不扣减。独立 `--suite b4-reference` 三场景七轮保留旧 B4 原判分；首轮 GLM 1/3、DeepSeek 2/3，修复后 GLM 诊断遇到新连接错误、usage 未知，预算已冻结，最终源码的完整真实验收仍待补。离线全仓 765 项、前端 77 项和浏览器 13 项通过。评测 `--timeout` 已补为连接与整个流的总截止时间，超时缺用量后停止后续请求。详见[草稿增量验收](reports/acceptance/2026-10-17-draft-reference-contract.md)。

B5 已接通只读取消资格查询：`assess_order_cancellation` 返回订单事实、有效政策版本和证据缺口，网页可展开引用；查询不取消订单、不申请审批。明确取消仍使用原预览与审批流程。首次[模型对照验收](reports/acceptance/2026-10-12-model-comparison.md)中，GLM 完整基线 35/35（≈¥0.033）、DeepSeek 34/35（≈$0.021，multi-06 步数超限），B5 七用例 GLM 5/7、DeepSeek 6/7，原失败保留。2026-10-17 的本轮 B5 复测两模型均 7/7，见下文；原 35 条 baseline 未在本轮重跑。对照入口 `uv run python -m evals.compare --strict <报告A> <报告B>`（币种各自列示）。仅补元数据且片段完全一致时可运行 `uv run --package rag python -m rag refresh` 复用原向量；正文或嵌入模型变化须重新 build。

政策问答的 `search_policy` 现以证据卡直接回显查询时间、编号原文、来源、版本、生效区间与检索分数，并高亮查询词的字面匹配；取消资格引用共用相同展示。历史恢复保留原快照，无命中或检索不可用时明确显示证据缺口。此次完成离线工程与浏览器验证，未新增真实模型或嵌入请求。见 [RAG 证据回显验收](reports/acceptance/2026-10-17-rag-evidence.md)。

2026-10-17 补强 B5 选单与回答规则：多笔订单先由用户确认目标，等待选单时不检索 / 概述政策；资格答复说明审批与未执行边界。新评测 JSON 保存 `visible_text`，支持直接复核回答与中断片段。用户提供新嵌入配置后，已重建 `qwen3.7-text-embedding-flash` 索引并校准阈值至 0.57（12/12），配置示例见 `.env.example`。完整 B5 **GLM 7/7、DeepSeek 7/7**，strict 对照通过，零写入尝试；一次完整通过不代表长期稳定率。见[B5 修复验收](reports/acceptance/2026-10-17-b5-response-reliability.md)与[嵌入校准](reports/rag/20261017-qwen-embedding-calibration.md)。

新订单和报价共用数量折扣与十进制舍入口径：同 SKU 合并数量后计算折扣单价，四舍五入到两位再乘数量；历史成交价和幂等重放结果保持原值。报价不锁价或预留库存。政策源文件更新后默认检索会拒绝旧索引，须运行 `uv run --env-file .env --package rag python -m rag build` 重新生成索引（消耗嵌入 token；`--env-file` 显式加载配置）。

M6–8 的 LangGraph + PostgreSQL HITL 已实施。基线验收见[2026-10-07 报告](reports/acceptance/2026-10-07-baseline.md)。崩溃恢复矩阵已在隔离 PostgreSQL 上通过，见[2026-10-08 报告](reports/acceptance/2026-10-08-crash-recovery.md)；[ADR-0010](docs/adr/0010-crash-recovery-reconciliation-scope.md) 仍待评审。下一阶段方向（多模型横向对比 · RAG 政策问答 · 评测迭代曲线 · 可展示部署收口）见 [tasks/plan-next-phase.md](tasks/plan-next-phase.md)，当前批次清单见 [tasks/todo.md](tasks/todo.md)；历史恢复协议见 [tasks/plan.md](tasks/plan.md)，当前运行时边界见 [ADR-0009](docs/adr/0009-langgraph-runtime.md)。

## 路线图

| 里程碑 | 内容 |
|---|---|
| M1–2 | 手写 agent loop：流式、工具调用、结构化输出、本地 trace |
| M3–5 | FastMCP Server + 工具设计精研（15~20 个工具）+ 评测集起步 |
| M6–8 | ✅ LangGraph 重构编排 + PostgreSQL checkpointer + HITL 审批流 |
| M9–11 | 评测驱动迭代（成功率曲线、成本优化）+ prompt injection 防护 |
| M12 | 部署上线、项目档案页与系列文章收口 |

## 文章与决策

- 所有架构决策记录在 `docs/adr/`（0001–0010，含 LangGraph/PostgreSQL HITL 与崩溃恢复范围），范围冻结与启动计划见 [`docs/agent-project-plan.md`](docs/agent-project-plan.md)
- 评测集：人工标注标准 [`docs/eval-annotation-guide.md`](docs/eval-annotation-guide.md) + 工具卡 [`docs/tool-cards.md`](docs/tool-cards.md) + 错误自愈记录 [`docs/error-recovery-log.md`](docs/error-recovery-log.md)
- 系列文章发布于个人站点 [Vie](https://vie-vibe.cn)，文稿随仓库维护：
  1. 《手写 Agent Loop：从一次 API 调用到多步任务》—— [`docs/articles/01-handwritten-agent-loop.md`](docs/articles/01-handwritten-agent-loop.md)
- 撰写规则见 [`docs/articles/writing-rules.md`](docs/articles/writing-rules.md)（禁虚构、客观口吻、技术正确性、发布前核对清单）

## 崩溃恢复与对账

graph checkpoint 保存执行状态，ERP mutation token 记录业务提交结果，RunStore 只保存展示投影。契约见 [ADR-0010](docs/adr/0010-crash-recovery-reconciliation-scope.md)。2026-10-08 在隔离 PostgreSQL 上，R01–R06 硬崩溃矩阵 19/19 通过，四种写工具的提交窗口都有同次证据；R07–R10 由确定性组件测试覆盖。ADR 仍是提案、待评审。证据见[崩溃恢复验收报告](reports/acceptance/2026-10-08-crash-recovery.md)。

硬崩溃验收需要显式配置隔离的 `ERPILOT_TEST_CHECKPOINT_DATABASE_URL`。测试为每个场景创建独立 PostgreSQL schema，并使用临时 ERP SQLite 库。多 API worker、分布式执行权和自动业务补偿不在本阶段支持范围内。

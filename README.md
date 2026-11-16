# Erpilot · 面向企业业务系统的可插拔 Agent 框架

通过可安装的业务模块，将自然语言交互接入 ERP、CRM、OA、工单系统和企业自建服务。Erpilot 提供会话、工具调用、人工确认、执行恢复和操作记录，业务模块定义工具、接口映射、权限规则与提交结果核对，用户通过同一套工作台完成查询和确认。

仓库提供订单与库存参考实现、连接独立 HTTP 服务的报销示例，以及基于固定数据的工单查询插件脚手架。每个应用实例启动时加载一个业务模块，CRM、OA 和其他目标系统可在独立插件仓库中实现同一接入契约。 [业务模块开发与接入](docs/business-modules.md)

面向 **Agent 应用开发 / Python 后端工程**的求职作品。模型负责提出工具调用，应用负责核对来源、业务规则与审批，数据库负责记录最终事实。

[三分钟演示](docs/demo-guide.md) · [实际架构](docs/architecture.md) · [面试案例](docs/interview-cases.md) · [投递材料](docs/application-kit.md) · [当前状态与证据](docs/project-status.md)

## 解决什么问题

业务系统需要验证模型提出的操作能否执行。应用先校验参数和权限，写入前等待人工确认，提交结果不明时按原请求号核对目标系统的记录。

下面以 ERP 订单演示说明这套流程。

1. 用户给出客户、商品、件数、备注与预算；应用查询真实商品和库存，生成带版本的草稿。
2. 多轮修改保留未变字段和来源；超出预算、商品或报价条件变化时提示待确认事项。
3. 用户核对完整版本并申请审批；批准后，事务再次复核价格、库存、版本及有效期。
4. 同次提交使用稳定幂等键；断线后恢复原审批，结果不明时先查业务提交事实。
5. 政策问题展示本轮有效证据和原文；没有依据时拒答。补货建议给出计算依据，不自动采购。

![离线演示：核对草稿版本 2 的完整明细后，等待独立人工审批](docs/assets/workspace-desktop.png)

截图来自实际本地离线演示：脚本模型、正式 API / MCP 和临时业务库。

## 一条命令启动演示

准备 **Python 3.12+、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js 22+（含 npm）**，在仓库根目录运行：

```sh
python scripts/quickstart.py
```

入口按 `uv.lock` 和 `package-lock.json` 安装、构建前端，再启动[本地页面](http://127.0.0.1:8765)。首次安装需要网络；演示不需要 `.env`、模型密钥、Docker 或 PostgreSQL。

页面选择“创建草稿”→“修改第二行”→“提交草稿”，刷新后先拒绝，再核对版本并重新批准。也可选“商品名称下单”“政策证据”“无依据拒答”“补货建议”。[逐步讲解及预期金额](docs/demo-guide.md)。

**演示使用脚本模型、关键词演示向量及独立临时库**，经过正式 API / LangGraph / MCP / 业务事务。只支持页面提供的固定指令；它验证工程流程，真实模型表现另见独立报告。Ctrl+C 关闭，重启清空演示数据。

```sh
python scripts/quickstart.py doctor                    # 只读环境诊断
python scripts/quickstart.py demo --skip-install        # 复用依赖，重新构建
python scripts/quickstart.py demo --port 8877            # 指定端口
python scripts/quickstart.py verify                     # 离线工程验收及日志
python scripts/quickstart.py verify --browser            # 另含匹配浏览器与端到端验收
```

## 带账号和持久恢复的容器演示

安装 Docker / Compose 后，在仓库根目录运行：

```sh
docker compose up --build -d --wait
docker compose exec api python -m erpilot_api.users add demo-owner --role approver
```

交互设置密码后，打开 [http://127.0.0.1:8768](http://127.0.0.1:8768) 登录。前端/API 同源，
账号隔离自己的会话、草稿和审批；操作员只能查询与准备草稿，审批员可执行自己的任务。
默认仍为无付费请求的脚本模型，但 ERP 数据和 PostgreSQL checkpoint 持久化，可重建容器恢复审批。
没有默认应用账号；基础镜像不包含密钥。真实模型覆盖配置、备份和浏览器验收见[部署指南](docs/deployment.md)。

## 架构与执行边界

```mermaid
flowchart TD
    UI[React 业务工作台] <-->|HTTP / SSE| API[FastAPI 宿主]
    API --> GRAPH[LangGraph 运行时]
    API --> VIEW[(RunStore · 会话与操作记录)]
    GRAPH <--> LLM[OpenAI 兼容模型]
    GRAPH <--> PG[(PostgreSQL checkpoint)]
    GRAPH --> MODULE[业务模块 · 工具 / 权限 / 结果核对]
    GRAPH --> TRACE[执行追踪]
    MODULE --> SYSTEM[目标系统 · ERP / CRM / OA / 工单 / 自建服务]
```

离线演示显式使用内存 checkpoint 和临时 SQLite；正式模型 API 要求 PostgreSQL，缺少时拒绝启动。`agent_core` 不依赖 ERP 或 MCP 业务包。完整数据流、失败窗口与限制见[架构说明](docs/architecture.md)。

| 技术选择 | 承担的职责与理由 |
|---|---|
| Python 3.12 / FastAPI / Pydantic | 异步工具与流式响应，入口和业务结构可验证 |
| LangGraph | 持久化执行状态和审批中断；保留自定义工具协议与业务约束 |
| FastMCP | 真实工具发现、参数 schema 和协议调用；风险与会话权限由应用绑定 |
| SQLite / SQLAlchemy | 小规模业务事实、版本和幂等同事务提交；单进程范围易复现 |
| PostgreSQL checkpoint | 保存可恢复的图状态；与业务数据库的职责分别明确 |
| 轻量 JSON 向量索引 | 小型政策语料可直接核对；有 BM25 / RRF 对照后再决定复杂度 |
| OpenAI 兼容 SDK | 用配置切换供应商；当前无 LiteLLM 路由服务 |
| React / TypeScript / Ant Design X | 流式工作台、业务卡片和响应式审批界面 |
| pytest / Playwright / 自建评测 | 分别核对业务事实、用户操作和真实模型表现；保留失败与未知用量 |

[最初选型及实施核对](docs/adr/0001-tech-stack.md) · [当前交付范围决策](docs/adr/0011-local-portfolio-delivery.md)

## 实现与取舍

- **将写权限放在模型之外**：写工具必须绑定代码审批门；模型请求只产生待批准动作。报价、保存草稿和政策查询都不等于批准订单。
- **让批准绑定不可变事实**：指定草稿版本、完整参数和稳定 token；批准后仍在业务事务内复核，库存变化时停止，不自行改版继续执行。
- **恢复先查业务事实**：graph checkpoint 保存执行状态，mutation token 证明业务是否提交，RunStore 只服务展示；三者不互相冒充权威来源。
- **约束回答的证据来源**：政策答复校验本轮引用、版本、原文及哈希，由程序渲染；仍不宣称已经解决任意自然语言的语义蕴含。
- **用可重放结果解释模型表现**：固定用例、逐轮 trace、数据库前后状态、原币种费用与缺 usage 熔断；不把不同版本的成功样例拼成一个通过率。

具体难点、源码入口、失败归因和面试追问见[三个实现案例](docs/interview-cases.md)。项目描述应按本人实际参与范围使用，见[求职投递材料](docs/application-kit.md)。

## 验证

`verify` 默认执行静态检查、Python 离线测试、前端协议与渲染测试、构建及 **11 个离线业务场景**。加 `--browser` 后安装匹配 Chromium，运行工作台和真实 API 的浏览器验收。逐步日志、JUnit 和汇总留在 `artifacts/verification/`；任何步骤失败返回非零状态。

PostgreSQL 恢复测试仅在显式设置隔离的 `ERPILOT_TEST_CHECKPOINT_DATABASE_URL` 后运行；未配置时会标注跳过。真实模型用例默认排除，浏览器中另有两个需独立实验服务的语义预览用例，未配置时单列跳过。[完整检查与故障处理](docs/development.md)。

历史验收按运行批次记录，见 [Git 历史验收报告](https://github.com/VieVibe/Erpilot/tree/b076ee4ff16c4338130f25db7e320abb62fc7e87/reports)。

## 使用真实模型开发

```sh
uv sync --all-packages --frozen
# 将 .env.example 复制成 .env，配置模型 key；Windows 可用 Copy-Item
docker compose up -d postgres
uv run --package erpilot-api python -m erpilot_api
# 另开终端
npm --prefix apps/web ci
npm --prefix apps/web run dev
```

访问[开发工作台](http://localhost:5173)。`ERPILOT_MODULE` 留空时，工作台显示「尚未配置业务模块」。该模式依然使用配置的 PostgreSQL checkpoint，业务对话在接入插件后启用。

要运行订单示例，先执行 `uv run --package erp-store python -m erp_store seed`，然后在 `.env` 设置 `ERPILOT_MODULE=order-example` 并重启 API。订单示例默认只读，显式设置 `ERPILOT_WRITES=1` 可演示写入审批。模型、嵌入及语义解析配置见[开发指南](docs/development.md)和[语义配置](docs/semantic-parsing.md)。接入目标系统的配置和工单插件脚手架见[业务模块开发指南](docs/business-modules.md)。

CLI：`uv run erpilot chat "查询商品 A1001 的库存"`。独立 MCP：`uv run --package mcp-erp python -m mcp_erp serve`。更多命令见[开发指南](docs/development.md)。

## 仓库导航

```text
apps/api/             FastAPI：会话、SSE、恢复、业务路由
apps/web/             React：流式对话、草稿、审批与证据卡
apps/cli/             终端交互与 trace 回放
packages/agent_core/  业务无关的图运行时、工具契约、审批和计量
packages/mcp_erp/     MCP 服务、桥接、会话与来源约束
packages/erp_store/   业务模型、草稿版本、事务、幂等和意图状态
packages/rag/         政策语料、检索、版本与引用校验
packages/evals/       冻结任务、真实模型评测、计量与质量门槛
scripts/              启动、离线演示和可校验交付
docs/                 架构、ADR、演示、技术文章和面试材料
```

当前支持**本地账号、单 API 执行进程**及容器复现。账号隔离工作会话，目标系统的数据权限由插件和目标系统共同执行。订单演示使用共享门店数据；
多租户、跨进程执行权和自动业务补偿未交付。默认核心任务已通过受控真实验收；语义工程专项通过，
语义预览已默认接入，原120条工程预期已完成委托AI审核，不据小样本宣称通用准确率。
[能力边界](docs/architecture.md) · [可校验交付包](docs/portfolio-overview.md)

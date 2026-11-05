# 开发、复现与验收

## 环境与推荐入口

Python 3.12+、uv、Node.js 22+ / npm。运行 `python scripts/quickstart.py doctor` 检查工具；
只读诊断不导入业务应用、不读取 `.env`、不连接模型或数据库。

```sh
python scripts/quickstart.py                   # 按锁安装、构建、启动
python scripts/quickstart.py demo --skip-install --port 8877
python scripts/quickstart.py verify --browser  # 完整离线工程范围
```

启动入口使用脚本位置定位仓库，不依赖终端当前目录；命令行中的脚本路径仍需指向实际文件。
依赖分别由 `uv.lock` / `package-lock.json` 确定，不在启动过程中升级。
`--skip-install` 跳过安装，但继续构建；只复用已安装的环境，不是缺依赖时的降级机制。

离线演示只监听 `127.0.0.1`，端口已占用时停止，避免接入另一个运行中的业务服务。
固定指令、金额、审批和重启行为见[演示指南](demo-guide.md)。

带账号、持久数据和容器重建恢复的运行入口见[部署指南](deployment.md)。该入口只依赖 Docker，
默认仍无付费调用；独立 `npm run test:deployment` 会重建指定测试项目的 API，不纳入本机临时演示的默认验收。

## 验收范围与记录

统一入口依次执行：Python 静态检查 → pytest 排除 `eval` → 前端协议 / 渲染测试 →
生产构建 → 十一场景实际 API / MCP / 临时库断言。`--browser` 追加匹配 Chromium 安装、
工作台回归和正式离线 API 浏览器验收。验收子进程设置 CI 模式，不复用已有端口上的服务。
Linux 若缺浏览器系统库，可先在 `apps/web` 运行
`npx playwright install --with-deps chromium`，再重新验收。

每次创建独立 `artifacts/verification/<UTC时间-标识>/`：

- `verification.json`：范围、锁文件哈希、执行步骤、耗时、退出状态；失败不记录为通过。
- `01.log` 等：每个执行步骤的完整标准输出与错误。
- `python.xml`：pytest JUnit，含实际通过、失败与跳过。
- `tmp/`、`cache/`：本次隔离的 pytest 数据，避免复用旧系统临时目录权限。

默认不会运行真实模型验收。未设置 `ERPILOT_TEST_CHECKPOINT_DATABASE_URL` 时，PostgreSQL
专项跳过；工作台测试中两个真实语义预览用例需要独立实验服务 `ERPILOT_PREVIEW_TEST_API`，
未提供时也跳过。其余工作台使用测试 API，`test:demo` 使用正式离线 API，两类结果分别列示。
“离线工程通过”只指本次覆盖范围，不代表模型准确率。真实 API 现按
[新启用标准](adr/0013-default-semantic-proposals.md)默认接入受约束预览。
原120条冻结数据不改写，当前已完成委托AI审核，见[标签状态与验证](label-review.md)。

离线测试不加载仓库 `.env`。真实评测的本地配置在选中的 `eval` 测试初始化时才读取，
模块结束后恢复环境；被排除的测试在收集阶段不会改变其他测试的运行模式。
CLI 和 API 入口的离线测试使用明确的配置与客户端替身。

浏览器测试截图使用 Playwright 的独立输出目录，不覆盖历史 `reports/acceptance/`。
CI 在浏览器验收后检查已记录报告无差异，防止测试污染旧证据。

需要单独排查时，在仓库根目录使用：

```sh
uv run ruff check .
uv run pytest
npm --prefix apps/web test
npm --prefix apps/web run build
uv run python scripts/demo.py
npm --prefix apps/web run test:ui -- --workers=2
npm --prefix apps/web run test:demo
```

## PostgreSQL 集成与硬崩溃恢复

只使用明确隔离的测试实例。示例启动独立容器，不复用正式 ERP 或开发 checkpoint 数据：

```sh
docker run -d --name erpilot-tests -p 127.0.0.1:55434:5432 -e POSTGRES_USER=erpilot -e POSTGRES_PASSWORD=local_test_only -e POSTGRES_DB=erpilot_test postgres:17
```

将 `ERPILOT_TEST_CHECKPOINT_DATABASE_URL` 设置为
`postgresql://erpilot:local_test_only@127.0.0.1:55434/erpilot_test`。
PowerShell 使用 `$env:ERPILOT_TEST_CHECKPOINT_DATABASE_URL = '连接串'`；Bash 使用
`export ERPILOT_TEST_CHECKPOINT_DATABASE_URL='连接串'`。随后运行统一验收或专项：

```sh
uv run pytest apps/api/tests/test_process_recovery.py apps/api/tests/test_intent_mode_postgres.py
```

测试逐例创建和清理独立 schema，业务 SQLite 使用临时路径。故障测试会硬退出自身的子进程，
检查事务前后窗口、同 token 对账、审批恢复及模式回退。测试后关闭本例容器：`docker rm -f erpilot-tests`。
无需为默认演示启动此服务。[恢复原理](architecture.md)。

## 真实模型、CLI 与 RAG

复制 `.env.example` 为 `.env`，按说明填写模型供应商、独立嵌入密钥和 checkpoint URL。
真实 API 使用 `python -m erpilot_api` 入口，它负责加载配置并在 Windows 使用 PostgreSQL 兼容事件循环。

```sh
uv sync --all-packages --frozen
docker compose up -d postgres
uv run --package erp-store python -m erp_store seed
uv run --package erpilot-api python -m erpilot_api
```

另开终端执行 `npm --prefix apps/web ci`、`npm --prefix apps/web run dev`，访问
[开发工作台](http://localhost:5173)。种子操作针对本地 `data/erpilot.db`，演示入口使用独立临时库。
首次启用正式业务库前先确认路径；有既有数据时不要为了展示反复 seed。

真实嵌入会产生供应商请求和费用。配置 `EMBED_*` 后显式构建：

```sh
uv run --env-file .env --package rag python -m rag build
```

检索查询必须沿用建索引时的模型和维度；换模型后重新构建并校准阈值。仅元数据变化、正文和片段
完全相同才可使用 `rag refresh`。离线演示在内存生成自己的关键词向量，不消费这个真实索引。

```sh
uv run erpilot chat "查询商品 A1001 的库存"
uv run erpilot chat --writes "给商品 A1001 入库 5 件"
uv run erpilot replay traces/某个文件.jsonl
uv run --package mcp-erp python -m mcp_erp serve
```

正式 API 默认 `ERPILOT_WRITES=0`；开启后写工具仍需要独立人工审批。
真实 API 启动自动接入语义预览，默认 `ERPILOT_INTENT_MODE=enforce`、`ERPILOT_SEMANTIC=1`。
候选预算独立于普通聊天，配置与关闭见[使用指南](semantic-parsing.md)。离线演示仍免付费；
新真实验收另建报告，不覆盖旧冻结批次。
[意图分析范围](intent-analysis.md) · [模型评测门槛](eval-quality-gates.md)。

## 常见问题

| 现象 | 处理 |
|---|---|
| 缺少 uv / npm，或 Node 版本过低 | 安装前置工具，重新打开终端，运行 `doctor`；入口不代装全局工具 |
| 测试临时目录拒绝访问 | 使用统一 `verify` 的独立目录；不需要修改已有目录权限 |
| Playwright 提示浏览器不存在 | 使用 `verify --browser` 安装匹配版本；不要复用版本不匹配的缓存 |
| 演示端口占用 | 使用 `demo --port 8877`，无需停止其他业务服务 |
| 演示重启后旧会话不可恢复 | 页面选择“开始新会话”；临时 checkpoint 不跨进程保存 |
| 正式 API 提示 checkpoint 不可用 | 检查 PostgreSQL 和显式 URL；正式 API 不自动退回内存 |
| 政策索引缺失或模型不匹配 | 重新检查嵌入配置并显式 build；默认离线演示不依赖它 |

交付源码快照和已有字幕视频见[作品入口](portfolio-overview.md)。实际付费调用结果、未知费用和旧失败
保存在独立报告，不能用本地工程验收覆盖。

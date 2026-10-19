# Erpilot 作品入口

Erpilot 是运行在 mini-ERP 上的审批型业务智能体：模型通过 MCP 查询事实、保存报价草稿与提出操作，业务层校验版本和价格，人工批准后才能执行订单与库存变更。项目定位为 Agent 应用 / 后端工程求职作品。

## 三分钟看项目

1. 按[演示指南](demo-guide.md)启动本地页面，展示草稿创建、修改、固定版本审批、拒绝后重提与建单。
2. 展示政策原文与无依据拒答、只读补货建议，区分模型判断、工具事实和业务执行。
3. 从[三个讲解案例](interview-cases.md)进入实现与失败证据，再读[最新状态](project-status.md)。

录屏交付物生成在 `artifacts/portfolio/`：`erpilot-demo.webm`、中文字幕 `erpilot-demo.vtt`、章节时间表 `chapters.json` 与页面截图。录屏调用正式本地 API、真实 MCP 与临时 SQLite，使用脚本模型和演示向量；不发付费请求，也不展示 PostgreSQL 硬崩溃恢复。

## 证据导航

| 能力 | 实现 / 演示入口 | 可核对证据 |
|---|---|---|
| 审批、版本与幂等 | `erp_store.drafts`、`agent_core.graph`；草稿到审批演示 | [订单草稿](../reports/acceptance/2026-10-11-order-draft.md)、[写入可靠性](write-reliability.md) |
| 状态恢复与业务提交对账 | graph checkpoint、mutation token、RunStore 投影 | [19 项进程恢复记录](../reports/acceptance/2026-10-08-crash-recovery.md)；ADR-0010 仍待评审 |
| 多轮草稿指代 | 持久任务查询、版本 / SKU guard | [B4 指代专项](../reports/acceptance/2026-10-19-b4-final-validation.md)：两模型各 3/3 场景、7/7 轮 |
| RAG 与证据绑定 | `search_policy`、结构化选证、程序原文渲染 | [最新政策验收](../reports/acceptance/2026-10-19-structured-policy-and-resume.md)：两模型各 11/13，门槛未达成 |
| 补货建议 | 销量 / 库存 / 采购周期计算，无采购写入 | [B6 最终真实验收](../reports/acceptance/2026-10-19-live-model-validation.md)：两模型各 7/7，一组成对样本 |
| 上下文与输入成本 | [固定场景实验](context-experiment.md)，可选结构化视图 | [本批验收](../reports/acceptance/2026-10-19-context-and-portfolio.md)，离线与真实结果分列 |

## 可校验源码快照

仓库包含尚未提交的工作区变更。本次使用当前源文件生成独立 ZIP，不改变 Git 的暂存区、分支或提交。快照按显式路径白名单包含源码、锁文件、文档和已有报告；不包含 `.env`、本地业务库、traces、安装依赖或个人工具配置。

```sh
python scripts/portfolio_release.py --output artifacts/erpilot-portfolio-20261019.zip
python scripts/portfolio_release.py --verify artifacts/erpilot-portfolio-20261019.zip
```

ZIP 内 `RELEASE-MANIFEST.json` 逐文件记录路径、大小和 SHA-256；相同文件字节及压缩环境重复构建可得到相同 ZIP。解压后先执行 `python scripts/portfolio_release.py --verify .`，再运行：

```sh
uv sync --all-packages --frozen
npm --prefix apps/web ci
npm --prefix apps/web run build
uv run python scripts/demo.py --serve --port 8765
```

依赖首次安装需要网络；源码包不内置运行环境、付费配置、真实嵌入索引或历史原始 trace。报告中的旧 trace 路径只作原验收定位，不能在源码包中重新查阅不存在的文件。完整源码复现与逐字节重现模型答复是不同范围。

重新录屏前安装 Playwright 对应的 Chromium / FFmpeg，然后执行：

```sh
npm --prefix apps/web exec -- playwright install chromium ffmpeg
npm --prefix apps/web run record:demo -- artifacts/portfolio-recapture
```

已有兼容浏览器可通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定。`PLAYWRIGHT_BROWSERS_PATH` 可将录屏依赖安装到项目临时目录。录屏脚本使用独立端口 8767，拒绝连接已有服务，结束时关闭自己启动的服务。

项目当前范围为本机可信单用户、单 API 进程；身份认证、多租户和多 worker 执行权需要独立设计。真实模型固定用例通过不等于生产可用或长期成功率。

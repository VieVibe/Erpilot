# Erpilot 作品入口

Erpilot 是运行在 mini-ERP 上的审批型业务智能体：模型通过 MCP 查询事实、保存报价草稿与提出操作，业务层校验版本和价格，人工批准后才能执行订单与库存变更。项目定位为 Agent 应用 / 后端工程求职作品。

## 三分钟看项目

在仓库根目录运行 `python scripts/quickstart.py`，即可按锁文件安装、构建并启动无密钥本地演示。
运行条件、失败排查和工程验收见[开发指南](development.md)。

2026-11-04 的[最新真实验收](../reports/acceptance/2026-11-04-real-model-reconnect.md)为订单 4/4、34/34，
语义创建/修改 7/7、6/6，以及带账号的真实 HTTP 建单。随后完成[语义默认启用收尾](semantic-parsing.md)。
随后完成[120条委托AI标签审核](label-review.md)，当前已无用户待审项。
最终打磨源码包为 `artifacts/erpilot-finished-20261104.zip`，
带本轮新录制字幕视频的交付包为 `artifacts/erpilot-delivery-finished-20261104.zip`。
视频使用脚本模型和临时库，旧录屏与交付包继续保留。
这些是本机构建产物，不随 Git 分发；clone 后按下文命令重新生成。
标签审核、语义默认启用、真实验收和部署补强的旧包继续保留，历史结果见[项目状态](project-status.md)。

1. 按[演示指南](demo-guide.md)启动本地页面，展示草稿创建、修改、固定版本审批、拒绝后重提与建单。
2. 展示政策原文与无依据拒答、只读补货建议，区分模型判断、工具事实和业务执行。
3. 从[三个讲解案例](interview-cases.md)进入实现与失败证据，再读[最新状态](project-status.md)。

投递用项目描述、三个技术取舍和面试演练入口见[求职投递材料](application-kit.md)。
连续多轮真实模型与临时 ERP 的验证另见[业务任务上下文验收](context-workflow.md)。

录屏交付物生成在 `artifacts/portfolio/`：`erpilot-demo.webm`、中文字幕 `erpilot-demo.vtt`、章节时间表 `chapters.json` 与页面截图。录屏调用正式本地 API、真实 MCP 与临时 SQLite，使用脚本模型和演示向量；不发付费请求，也不展示 PostgreSQL 硬崩溃恢复。

## 证据导航

| 能力 | 实现 / 演示入口 | 可核对证据 |
|---|---|---|
| 审批、版本与幂等 | `erp_store.drafts`、`agent_core.graph`；草稿到审批演示 | [订单草稿](../reports/acceptance/2026-10-11-order-draft.md)、[写入可靠性](write-reliability.md) |
| 状态恢复与业务提交对账 | graph checkpoint、mutation token、RunStore 投影 | [19 项进程恢复记录](../reports/acceptance/2026-10-08-crash-recovery.md)；ADR-0010 仍待评审 |
| 多轮草稿指代 | 持久任务查询、版本 / SKU guard | [B4 指代专项](../reports/acceptance/2026-10-19-b4-final-validation.md)：两模型各 3/3 场景、7/7 轮 |
| RAG 与证据绑定 | `search_policy`、结构化选证、程序原文渲染及受阻直接结束 | [新验收](../reports/acceptance/2026-10-28-contracts-and-policy.md)：GLM v4 独立 13/13；旧 v3 两模型各 11/13 保留 |
| 补货建议 | 销量 / 库存 / 采购周期计算，无采购写入 | [B6 最终真实验收](../reports/acceptance/2026-10-19-live-model-validation.md)：两模型各 7/7，一组成对样本 |
| 上下文与输入成本 | [固定场景实验](context-experiment.md)，可选结构化视图 | [本批验收](../reports/acceptance/2026-10-19-context-and-portfolio.md)，离线与真实结果分列 |

## 可校验源码快照

快照按构建时的实际工作区生成，内容身份由逐文件清单确定；Git 提交号只作历史定位。
构建不改变暂存区、分支或提交。快照按显式路径白名单包含源码、锁文件、文档和已有报告；
不包含 `.env`、本地业务库、traces、安装依赖或个人工具配置。

## 统一投递包

先生成源码快照（不需要录屏文件）：

```sh
python scripts/portfolio_release.py --output artifacts/erpilot-finished-20261104.zip
python scripts/portfolio_release.py --verify artifacts/erpilot-finished-20261104.zip
```

视频、字幕和截图同样不随 Git 分发。本轮录屏位于 `artifacts/finishing/recording-final/`，
可连同当前源码组成交付 ZIP；全新 clone 请先按本页末尾的录屏命令生成这些文件。
输出存在时改用新的文件名，避免覆盖历史交付。

```sh
python scripts/portfolio_delivery.py --output artifacts/erpilot-delivery-finished-20261104.zip --media-dir artifacts/finishing/recording-final
python scripts/portfolio_delivery.py --verify artifacts/erpilot-delivery-finished-20261104.zip
```

交付包以 `START-HERE.md` 为入口，内含 `erpilot-source.zip`、媒体及
`DELIVERY-MANIFEST.json`。外层清单逐文件记录 SHA-256，核验时同时检查内部源码清单。
输出已存在时拒绝覆盖。视频为已有脚本化演示，连续多轮真实模型证据在源码报告中分别列示。

ZIP 内 `RELEASE-MANIFEST.json` 逐文件记录路径、大小和 SHA-256；相同文件字节及压缩环境重复构建可得到相同 ZIP。解压后先执行 `python scripts/portfolio_release.py --verify .`，再运行：

```sh
uv sync --all-packages --frozen
npm --prefix apps/web ci
npm --prefix apps/web run build
uv run python scripts/demo.py --serve --port 8765
```

依赖首次安装需要网络；源码包不内置运行环境、付费配置或历史原始 trace。
仓库已跟踪的政策向量索引会随源码打包，但不能替代匹配的嵌入配置或授权查询；离线演示不读取该索引。
报告中的旧 trace 路径只作原验收定位，不能在源码包中重新查阅不存在的文件。完整源码复现与逐字节重现模型答复是不同范围。

新 clone 或重新录屏前先同步 Python / 前端依赖、完成前端构建，再安装 Playwright 对应的 Chromium / FFmpeg：

```sh
uv sync --all-packages --frozen
npm --prefix apps/web ci
npm --prefix apps/web run build
npm --prefix apps/web exec -- playwright install chromium ffmpeg
npm --prefix apps/web run record:demo -- artifacts/finishing/recording-final
```

已有兼容浏览器可通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定。`PLAYWRIGHT_BROWSERS_PATH` 可将录屏依赖安装到项目临时目录。录屏脚本使用独立端口 8767，拒绝连接已有服务，结束时关闭自己启动的服务。

项目另提供[带账号容器](deployment.md)，隔离各账号的会话、草稿与审批；ERP 仍为共享门店，单 API 进程。
多租户和多 worker 执行权未交付。真实模型固定用例通过不等于生产可用或长期成功率。

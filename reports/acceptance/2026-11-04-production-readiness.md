# 基础部署、最小身份与真实批次补强验收

日期：2026-11-04；授权和范围见[补强计划](../../docs/plans/2026-11-04-production-readiness.md)。

**本轮交付：带账号的同源容器、持久恢复、资源归属/审批权限及审计已通过工程验收。
真实模型新订单批次没有完整通过，连接错误缺 usage 后冻结；语义创建/修改未追加付费运行。**
数据规模保持当前范围，不迁移向量库，不扩展多 API 执行进程。

## 做了什么与原因

| 改动面 | 实现 | 原因 |
|---|---|---|
| API | 本地账号、登录退出、服务端会话、CSRF/Origin、归属过滤、角色与审计 | session_id 只能关联任务，不能证明操作者身份；授权在路由和工具层执行 |
| 网页 | 登录页、账号/角色、退出、按账号保存当前会话、统一带 CSRF 的 API 请求 | 账号切换后不能复用别人的当前草稿；过期登录回到登录入口 |
| 部署 | 前端/API 多阶段镜像、PostgreSQL、持久卷、实查健康、非 root/根只读、默认离线与显式真实覆盖 | 只依赖 Docker 即可复现；演示重建恢复无需付费模型 |
| 模型流 | 显式关闭内部异步生成器，消费者取消后立即释放 HTTP 流 | 本地复现取消后连接延迟释放，避免依赖异步生成器 GC |
| 文档与 CI | 部署指南、ADR-0012、当前边界、单独容器浏览器 CI、打包白名单 | 求职时能运行、演示并说明身份、业务授权和模型质量各自的证据 |

复用现有 Python/FastAPI/LangGraph/MCP/SQLite/PostgreSQL/React。密码采用标准库派生和随机盐，
服务端会话可直接撤销；同源镜像避免额外跨域部署。选型与限制见
[ADR-0012](../../docs/adr/0012-authenticated-container-deployment.md)。

## 真实模型：独立失败批次，保留原判定

模型 `glm-5.3-flash`，端点主机 `flashcoding.ai`。新批次使用既有冻结的订单 v2 四流程，
SDK 和用例重试均为 0，单请求总截止时间 45 秒。总计划估价 CNY 0.20，订单阶段上限 CNY 0.10。

| 指标 | 结果 |
|---|---|
| 完整任务 | 0/4；首流程尚未完成，其余流程未执行 |
| 阶段 | 计划 34，执行 7，通过 6 |
| 请求/计量 | 11 个请求，10 个有 usage；账本 22 条 started/settled/unknown 记录 |
| 已知估价 | CNY 0.009714，另 **1 笔未知**；不是供应商账单总额或硬上限 |
| 中止原因 | 第 7 阶段 `APIConnectionError: Connection error.`，缺 usage 后停止本轮后续付费生成 |
| 业务状态 | 失败阶段保存的前后业务快照哈希相同，没有把连接错误当成成功提交 |
| 源码一致性 | 该真实批次开始/结束一致；之后的账号、部署和流关闭改动没有经过新增付费模型复测 |

证据：[冻结执行计划](20261104-readiness-live-plan.json)、[原始报告](20261104-readiness-order-v2.json)、
[原话输入](20261104-readiness-order-v2-inputs.jsonl)、[计量账本](20261104-readiness-order-v2-requests.jsonl)、
[账本与报告完整性复核](20261104-readiness-live-audit.json)。完整性复核只查已保存材料，不改 oracle，也不请求模型。

运行时还观察到 `async generator already executing` 的异步清理警告。未直接复现该警告或证明连接异常根因。
另用实际 SDK + HTTP 替身流复现了消费者关闭后连接未立即释放：
[修复前回归失败](20261104-readiness-stream-close-before.txt)，加入显式 `aclosing` 后
[LLM 流专项 31 passed](20261104-readiness-stream-close-after.xml)。这只证明取消路径被修复，
不能改判旧真实批次或宣称上游连接问题已解决。

语义创建/修改候选仍默认 off，120 条人工标签继续待审；旧批次、未知费用和用户已跳过的核销保持原状态。

## 身份和业务边界

- 登录 cookie 为 HttpOnly / Strict；8 小时固定到期，退出/禁用可撤销。口令独立盐 + PBKDF2-HMAC-SHA256，
  600,000 次派生；token 只存摘要，CSRF 在浏览器内存中，账号由交互 CLI 创建，无默认应用密码。
- 服务端创建会话并绑定 owner；历史按 owner 在数据库分页。会话、草稿、意图/操作快照、审批和 run 事件
  均经所属会话授权；不能用自己的 session_id 包装别人的 pending_id，也不自动认领旧匿名会话。
- operator 可以查询共享 ERP、准备自己的草稿，模型可见工具去掉风险写入，直接审批接口返回 403。
  approver 只可批准自己任务，不提供跨用户代审批或租户隔离。
- ERP 商品、库存和订单属于共享门店；账号隔离工作任务，不能对外表述为多租户权限体系。
- 审计绑定操作者和资源，记录审批对象、决策与 HTTP 状态；业务成功仍以 checkpoint / ERP 幂等事实为准。
  不保存密码、cookie、CSRF 或用户原话。失效登录、异源请求、错误/非 ASCII CSRF、异常 JSON 和超大请求均有负例。

## 最终工程验证

宿主 Windows，Python 3.12；数据库为本批专用 Compose 的 PostgreSQL 17，硬退出测试使用独立 schema。
镜像在 Docker 的 Linux 环境构建并实际运行。没有把 CI 配置提交前的本地验证说成远端 CI 已通过。

| 检查 | 结果 | 证据 |
|---|---|---|
| Ruff / Git whitespace | 通过 | [结构化自审](../review/20261104-production-readiness.md) |
| 全仓 Python（含 PG 硬退出恢复） | **1783 passed / 0 skipped / 47 live eval deselected**，431.04 秒 | [JUnit](20261104-readiness-python.xml) |
| 账号负例专项 | 6 passed；含恶意请求、Secure cookie、撤销/到期/限流 | [JUnit](20261104-readiness-auth.xml) |
| 前端协议/组件 | **112 passed**，构建通过；入口约 449 KB gzip | [测试](20261104-readiness-frontend.txt)、[构建](20261104-readiness-build.txt) |
| 工作台浏览器 | **57 passed / 2 skipped**；两项依赖独立实验服务 | [日志](20261104-readiness-workspace.txt) |
| 正式离线 API 浏览器 | **5 passed** | [日志](20261104-readiness-browser.txt) |
| 真实容器浏览器 | **3 passed**：320/1440px 登录切换/只读角色、重建后原审批和版本恢复、重复批准 409 | [日志](20261104-readiness-deployment.txt) |
| 离线业务场景 | **11/11**，正式 API/MCP/临时 ERP | [原始汇总](20261104-readiness-demo.json) |
| 部署切换与实查探针 | 默认容器不注入模型/嵌入密钥；显式 live 启动健康且写关闭；两者均强制认证 | [脱敏探针](20261104-readiness-deployment-probes.json) |
| 依赖故障 | PG 停止时 healthz=200、readyz=503，恢复后 readyz=200 | [探针与构建信息](20261104-readiness-deployment-probes.json) |

live 配置的部署验证只访问登录、runtime 和健康接口，没有发起模型生成/检索请求，不违反未知 usage 后停止付费生成的约定。
容器浏览器账号随机生成、经 stdin 创建，测试后禁用；截图在独立测试目录。
走查修正了持久容器误用“重启清空”提示、登录输入框的无障碍名称，以及审批金额定位包含动态计数的问题。
原有 reports 的已跟踪文件没有修改。

## 复现与交付

```sh
docker compose up --build -d --wait
docker compose exec api python -m erpilot_api.users add demo-owner --role approver
```

交互设置密码后访问 `http://127.0.0.1:8768`。镜像、账号、角色、数据备份和测试命令见
[部署指南](../../docs/deployment.md)。不依赖 Docker 的临时演示继续使用 `python scripts/quickstart.py`。

源码包 `artifacts/erpilot-readiness-20261104.zip` 和带已有字幕视频的
`artifacts/erpilot-delivery-readiness-20261104.zip` 独立生成，不覆盖前一阶段包。
视频保留历史临时匿名演示口径，新部署/权限以本报告和源码为准。
哈希、逐文件清单核验及敏感配置排除检查保存在 `artifacts/erpilot-readiness-20261104-validation.json`。
测试使用项目 `erpilot-readiness-20261104`，结束后清理其容器和合成数据卷，释放 8768/5433。

## 当前项目成熟度自评

| 用户标准 | 当前 / 10 | 依据与剩余项 |
|---|---:|---|
| 功能完整 | **8** | 默认核心闭环、身份和部署完整；真实模型整单及语义候选完整门槛仍未通过 |
| 工程质量 | **9** | 锁文件、镜像、完整回归、PG 故障恢复、权限负例、独立 CI 与可核验交付 |
| 产品体验 | **9** | 手机/桌面、登录切换、角色提示、原版本审批恢复；首屏包体仍可优化 |
| 求职说服力 | **9** | 能展示身份→Agent→审批→事务→恢复，并解释成本、失败和技术取舍；不虚构生产业绩 |

适合展示为可复现、有权限和持久恢复的 Agent 求职作品。当前没有公开托管域名、组织/SSO、跨用户代审批、
多租户、多进程执行权或生产级运维。下一项实质性质量缺口仍是完整真实模型任务与人工复核，
工程测试数量不能代替这道门槛。

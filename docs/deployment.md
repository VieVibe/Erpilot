# 容器运行与账号管理

本入口将前端和 API 放入同一个镜像，PostgreSQL 单独运行。默认使用脚本模型和独立的合成 ERP 数据，
无需密钥，不发模型或嵌入请求。与临时本机演示相比，这里启用账号、持久草稿和 PostgreSQL checkpoint，
可演示容器重建后恢复待审批任务。它仍只有一个 API 执行进程。

## 启动

安装 Docker Engine / Docker Desktop 和 Compose v2，仓库根目录运行：

```sh
docker compose up --build -d --wait
docker compose exec api python -m erpilot_api.users add demo-owner --role approver
```

命令交互输入两次密码，至少 12 个字符。没有默认应用账号，也不把密码写入命令参数、镜像或源码。
省略 `--role` 时默认最小权限的 operator。
打开 [http://127.0.0.1:8768](http://127.0.0.1:8768)，登录后按[演示指南](demo-guide.md)操作。
仅需 Docker；宿主机无需安装 Python、Node 或 PostgreSQL。

默认映射回环地址：网页 8768、PostgreSQL 5433。`/healthz` 只证明 API 存活，`/readyz` 实查
checkpoint 和账号存储；`docker compose ps` 应显示两个服务 healthy。启动期间 PostgreSQL 不可用会拒绝服务，
不静默改用内存 checkpoint。

镜像按 `uv.lock` / `package-lock.json` 安装；API 以非 root 用户运行，根文件系统只读，可写数据放卷中，
临时文件在 `/tmp`。镜像构建不包含 `.env`、本地数据、运行 trace 或旧验收报告。

## 账号与业务权限

```sh
docker compose exec api python -m erpilot_api.users add draft-operator --role operator
docker compose exec api python -m erpilot_api.users disable draft-operator
```

| 角色 | 可以做什么 | 执行边界 |
|---|---|---|
| operator | 查询共享门店的商品、库存和订单，准备、修改自己的草稿 | 不能批准或执行 ERP 写入；服务端工具列表和审批接口均拦截 |
| approver | 上述能力，以及批准自己任务的 ERP 写操作 | 不能查看或代批其他账号的任务 |

**隔离的是账号的会话、草稿、审批、运行事件和历史；ERP 商品、库存、订单是同一家门店的共享业务数据。**
这不是租户隔离，也没有跨用户代审批。账号禁用会撤销所有登录；已开始的请求不提供即时强制终止。
旧匿名会话不会自动认领为某个账号，启用认证后从服务端创建的新会话开始。

浏览器使用 HttpOnly / SameSite=Strict cookie，8 小时到期；变更请求要求内存中的 CSRF token，
不把密码或 token 放 localStorage。按账号保存当前会话 ID，退出、切换账号和重新登录不会串入别人的草稿。
应用只接受 JSON 变更请求，限制请求体并拒绝异源请求；每个账号十分钟五次失败登录后限流。

`GET /api/auth/audit` 只返回当前账号最近 50 条记录，包含登录、访问拒绝、审批请求和 HTTP 状态。
HTTP 接受不代表业务成功，业务结果仍查图状态和 ERP 幂等记录。审计不存密码、cookie、CSRF 或用户原话。

## 持久化、重建与备份

| 卷 | 内容 |
|---|---|
| `app_data` | `/data/erpilot.db` 业务库；`erpilot-runs.db` 展示记录、账号、登录、归属与审计；`traces/` |
| `checkpoints` | PostgreSQL 图消息、待审批对象、原 token 和恢复状态 |

第一次启动只向新库初始化合成商品与订单；初始化中断不会发布半份业务库，后续启动不覆盖现有业务数据。
`docker compose up -d --force-recreate --wait api` 重建容器仍保留两类数据。
`docker compose down` 停止并移除容器，保留卷；`down --volumes` 会删除该项目全部演示数据。

备份时先 `docker compose stop api`，在无业务写入的窗口同时备份 `app_data` 与 PostgreSQL
（`pg_dump` 或停机后的卷备份）；恢复时两份使用同一备份窗口。只恢复其中一类状态不能证明业务可继续执行。
账号库同样是敏感数据，不能随源码包分发。

## 显式使用真实模型

复制 `.env.example` 为 `.env` 并配置模型和独立嵌入密钥，再运行：

```sh
docker compose -f compose.yaml -f compose.live.yaml up --build -d --wait
```

此时才将 `.env` 注入容器，`ERPILOT_RUNTIME=live`，认证仍强制启用。覆盖文件默认 `ERPILOT_WRITES=0`，
自由输入会使用真实模型并可能收费；确需写演示时在覆盖文件中显式改为 `1`，仍须账号权限和逐次审批。
真实模式默认开启语义创建/修改预览，仍需完整表单核对和独立审批。新端点缺价目时
保留基础解析，候选额度用尽不影响普通聊天。[配置、关闭与恢复](semantic-parsing.md)。
基础脚本演示免付费；真实模式启动和健康检查也不调用模型。
RAG 使用的模型和索引须匹配，配置及构建见[开发指南](development.md)。

默认 Compose 为本机复现，数据库密码也是本机示例。若扩展到公网，需另外配置同源 HTTPS 代理、
`ERPILOT_COOKIE_SECURE=1`、可信代理地址与独立数据库凭证；本轮没有交付公网域名或托管服务。
不使用多 API 副本或 `--workers`：当前进程内串行执行权不提供跨进程租约。

## 验收与故障排查

在**专用测试项目**上运行以下命令；浏览器测试会重建 API 容器，并创建后禁用随机测试账号：

```sh
docker compose --project-name erpilot-deployment-tests up --build -d --wait
cd apps/web
npm ci
npx playwright install chromium
```

PowerShell：

```powershell
$env:ERPILOT_DEPLOYMENT_PROJECT='erpilot-deployment-tests'
npm run test:deployment
```

macOS / Linux：`ERPILOT_DEPLOYMENT_PROJECT=erpilot-deployment-tests npm run test:deployment`。
测试覆盖 320 / 1440px 登录、账号切换、只读权限、刷新不重发，以及容器重建后同一版本审批恢复与重放拦截。
完成后回仓库根目录，`docker compose --project-name erpilot-deployment-tests down --volumes` 清理本次测试。
该验证也已加入 CI，使用独立项目。截图只写测试输出目录，不覆盖历史验收。

- 端口占用：停止占用 8768 / 5433 的服务，或修改 Compose 的宿主端口并为测试设置 `ERPILOT_DEPLOYMENT_URL`。
- 不健康：`docker compose logs --tail=80 api postgres` 查看启动错误；检查数据库连接和卷的写入权限。
- 登录失败：检查账号创建是否使用同一 Compose 项目；错误尝试过多等待十分钟；忘记密码可禁用旧账号、创建不同名称的新账号，新账号不继承旧任务。
- 自由输入没有理解：默认脚本模型只认固定指令，页面明确标注；真实模型需使用显式覆盖配置。

安全设计与取舍见 [ADR-0012](adr/0012-authenticated-container-deployment.md)，本批结果见
[部署与权限验收](../reports/acceptance/2026-11-04-production-readiness.md)。

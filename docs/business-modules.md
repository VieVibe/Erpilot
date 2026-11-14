# ERP 业务模块开发

Erpilot 提供会话、工具执行、人工确认、执行恢复和操作记录。业务模块负责参数、业务规则、ERP 接口调用和提交结果对账。每个应用实例在启动时选择一个模块。

默认启动加载订单与库存参考实现，保留草稿版本、报价核对、订单提交和政策检索。报销示例展示通过独立 HTTP 服务接入另一类业务。

## 运行报销示例

先按 README 安装依赖并配置模型和 PostgreSQL checkpoint。以下命令在仓库根目录的 PowerShell 执行。

第一个终端启动本地测试 ERP，它把报销单和幂等记录持久化到 `data/expense-example.db`。

```powershell
$env:ERPILOT_EXPENSE_BACKEND_TOKEN = 'replace-with-a-random-secret'
uv run --package erpilot-api python -m uvicorn erpilot_api.examples.expense_backend:create_app --factory --host 127.0.0.1 --port 8011
```

第二个终端启动 Erpilot。源码环境可以直接使用 `module:factory`：

```powershell
$env:ERPILOT_MODULE = 'erpilot_api.examples.expense:create_module'
$env:ERPILOT_EXPENSE_URL = 'http://127.0.0.1:8011'
$env:ERPILOT_EXPENSE_SERVICE_TOKEN = 'replace-with-the-same-random-secret'
$env:ERPILOT_WRITES = '1'
uv run --package erpilot-api python -m erpilot_api
```

重新安装包含最新入口配置的 `erpilot-api` 后，也可以把 `ERPILOT_MODULE` 简写为 `expense-example`。这个内置短名称与第三方插件使用同一套入口发现机制。

第三个终端启动工作台。

```powershell
npm --prefix apps/web run dev
```

打开工作台，输入“提交一笔交通费报销，用途为客户拜访，金额为 38 元”。确认工具参数中的金额为 3800 分，然后批准。结果应包含申请编号、请求号和 `pending_erp_approval`。Erpilot 的确认授权提交申请，ERP 后续审批由目标系统负责。再次使用相同请求号和参数提交，测试 ERP 返回原结果。

`ERPILOT_WRITES=0` 只注册查询工具。清除 `ERPILOT_MODULE` 后重启，恢复默认订单示例。现有脚本化 quickstart 演示继续使用订单场景。容器入口选择自定义模块时应设置 `ERPILOT_RUNTIME=live`，并让模块代码、配置和 HTTP 服务在容器中可访问。

## 开发自己的模块

创建可安装的 Python 包，导出无参数工厂，返回 `erpilot_api.modules.BusinessModule`。工厂可以是异步函数。开发阶段可将 `ERPILOT_MODULE` 设为 `your_company.erp:create_module`。发布的插件建议在自己的 `pyproject.toml` 注册入口：

```toml
[project.entry-points."erpilot.business_modules"]
my-erp = "your_company.erp:create_module"
```

安装这个包后设置 `ERPILOT_MODULE=my-erp` 即可加载。没有同名入口、存在重复入口或工厂返回类型错误时，Erpilot 会拒绝启动。

| 扩展点 | 开发者实现 |
| --- | --- |
| `name` / `title` | 稳定标识与工作台名称 |
| `instructions` | 业务术语、必填信息与结果解释 |
| `tools` | 工具目录，使用已有 `agent_core.tools.Tool` 和 Pydantic 参数模型 |
| `for_session(session_id)` | 兼容接口，返回绑定当前会话的工具处理函数 |
| `reconciler(session_id)` | 兼容接口，写入结果核对器，实现 `MutationReconciler.lookup` |
| `for_context(context)` | 推荐的新接口，按宿主校验过的会话与操作者身份绑定工具 |
| `reconciler_for_context(context)` | 推荐的新接口，按同一身份上下文创建写入结果核对器 |
| `connector_summary` | 可安全返回给工作台的连接状态摘要，不能包含地址凭据或令牌 |
| `health_check()` | 异步检查目标 ERP 依赖；部署 `/readyz` 会调用它 |
| `suggestions` | 工作台的示例输入 |
| `version` | 模块版本，升级业务契约时同步调整 |

可以直接复制 `apps/api/src/erpilot_api/examples/expense.py`，替换 HTTP 地址、请求映射和结果映射。调用方也可以通过 `create_app(business_module=...)` 直接装配。模块模式拥有工具和恢复配置，混用显式 `tools`、旧 ERP provider 或全局 reconciler 会报错。

工具名、描述、风险等级和参数 schema 应在不同会话与服务重启之间保持稳定。启动时 Erpilot 会调用一次会话或上下文工厂并与模块工具目录核对，发现变化就拒绝启动。所有写工具必须接受 `client_token`，并提供对账器。`/api/runtime` 返回模块版本和工具契约摘要，方便部署侧核对版本。每次启动选择业务模块后，应使用该模块专属的 RunStore 和 checkpoint 数据位置。切换不同业务模块时使用新的会话，旧模块的待审批任务应在原部署中完成。

## 写入和身份边界

写工具设置 `risk` 后通过现有图运行时进入人工确认。运行时把稳定请求号放入 `client_token` 参数，模块把它传到 ERP。仅在目标 ERP 确实支持幂等提交时设置 `retry_safe=True`。

对账返回 `found` 时附上已提交结果，返回 `conflict` 表示请求号对应的内容不一致。只有能够确定未提交时返回 `absent`。网络超时、服务不可用以及无法确定的查单结果应抛出异常，由运行时保持待核对状态。对于有延迟索引的真实 ERP，需要按它的提交事实核对能力实现此接口。

模块从服务端配置读取连接凭据。`BusinessSessionContext` 提供 `session_id`、`user_id`、`username` 和 `role`。启用 Erpilot 登录后，后三个字段来自宿主验证过的会话；未启用登录时为 `None`。模块应使用稳定的 `user_id` 映射 ERP 身份、组织与数据权限，不能把 `session_id` 当作 ERP 用户凭据。目标 ERP 仍须根据服务凭据或用户委托令牌再次鉴权，不能信任客户端直接传入的角色。

旧插件可以继续实现 `for_session` 和 `reconciler`。需要用户级权限或租户隔离的新插件可以只实现两个 context 工厂。账号被停用、会话不存在或不再有效时，宿主不会向模块开放工具，也不会创建写入对账器；operator 仍只获得只读工具，approver 才能获得写工具。

HTTP 插件可以复用 `HttpConnectorConfig`。它统一解析 `<PREFIX>_URL`、`<PREFIX>_SERVICE_TOKEN`、`<PREFIX>_TIMEOUT` 和 `<PREFIX>_VERIFY_TLS`，生成服务端 `Authorization` 以及经过校验的会话、用户和角色请求头。配置对象的字符串表示和公开摘要不会包含令牌；携带服务凭据访问非本机地址时强制使用 HTTPS。用户身份请求头只说明 Erpilot 已验证的操作者，目标 ERP 仍应验证服务令牌，并将用户 ID 映射到自己的账号、组织和权限。

报销示例后端可通过 `ERPILOT_EXPENSE_BACKEND_TOKEN` 启用 Bearer 校验，Erpilot 侧把相同秘密配置到 `ERPILOT_EXPENSE_SERVICE_TOKEN`。本机无令牌模式只用于快速联调。部署就绪检查会在数据库和 checkpoint 之外调用模块 `health_check`；ERP 无法连接、超时或凭据错误时 `/readyz` 返回 503，但 `/healthz` 仍只表示 Erpilot 进程存活。

测试 ERP 仅用于本地联调，没有接入企业认证。`expense_backend.py` 使用操作者 ID 与会话 ID 共同划分示例数据，并以同一 SQLite 事务保存申请和请求号，演示重复提交、参数冲突、操作者隔离及重启后的结果查询。

## 当前范围与后续迁移

当前已支持 Python 入口插件和 `module:factory` 两种加载方式、启动契约校验、带登录身份的会话工具与对账器绑定、失效账号安全关闭、HTTP 连接配置与服务凭据、ERP 健康检查、业务提示词、通用审批与恢复、模块工作台提示，以及默认订单示例的模块装配。自定义模块的 HTTP API 不挂载订单专用路由。

订单示例已通过 `order_module.py` 声明自己的路由、运行特性、状态增强和语义解析能力。通用会话服务只调用模块钩子，同时保留少量属性供旧验收脚本过渡使用。前端仍保留订单草稿组件，安装包也仍包含 `mcp_erp`。后续可按实际接入需求拆分前端扩展和安装依赖。模块前端目前使用通用参数确认和会话结果展示，业务专用表单可在后续接入时扩展。

单 API 执行进程约束继续适用。企业 SSO、租户隔离、多 worker 执行协调和动态插件安装需要独立实现与验证。

## 验证

```powershell
uv run python -m pytest apps/api/tests/test_business_modules.py apps/api/tests/test_graph_api.py apps/api/tests/test_live_defaults.py
npm --prefix apps/web run build
```

模块测试通过 HTTP ASGI transport 连接独立测试 ERP，覆盖批准与拒绝、待审批时重建宿主、重复提交、内容冲突、会话隔离、远端错误以及正式启动入口。模型使用固定测试响应，方便重复执行。

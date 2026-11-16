# 业务模块开发与系统接入

Erpilot 提供会话、工具执行、人工确认、执行恢复和操作记录。业务模块负责参数、业务规则、目标系统的接口调用和提交结果核对。ERP、CRM、OA、工单系统和企业自建服务共用同一插件契约。每个应用实例在启动时选择一个模块。

真实 API 默认启动工作台和会话服务，业务对话在配置业务模块后启用。在 `.env` 或进程环境中选择模块，修改后重启服务。

| `ERPILOT_MODULE` | 启动行为 |
| --- | --- |
| 未设置或留空 | 工作台显示「尚未配置业务模块」，无需模型密钥，继续使用框架的 PostgreSQL checkpoint 配置 |
| `order-example` | 启用订单、库存、草稿与审批参考实现。本地首次运行先执行 `uv run --package erp-store python -m erp_store seed` |
| `expense-example` | 启用报销示例，另需启动示例 ERP HTTP 服务 |
| 已安装插件名称或 `module:factory` | 加载开发者提供的插件 |

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

`ERPILOT_WRITES=0` 时，报销示例只注册查询工具。脚本化 quickstart 和基础 Compose 会显式运行离线订单场景。容器加载业务模块时使用 `ERPILOT_RUNTIME=live`，并让模块代码、配置和 HTTP 服务在容器中可访问。

## 开发自己的模块

在 Erpilot 仓库根目录生成一个最小只读插件：

```powershell
uv run erpilot plugin init acme-ticketing
```

命令会创建 `acme-ticketing/`，其中包含 Python 包、Erpilot 插件入口和安装指南。也可以用 `--output` 指定插件的父目录。按生成的 README 安装后，将 `ERPILOT_MODULE` 设为 `acme-ticketing`，即可用固定示例数据查询工单 `DEMO-001`。接入真实系统时，将查询替换为目标系统的接口调用，并按需要绑定操作者身份。

创建可安装的 Python 包，导出无参数工厂，返回 `erpilot_api.modules.BusinessModule`。工厂可以是异步函数。开发阶段可将 `ERPILOT_MODULE` 设为 `your_company.ticketing:create_module`。发布的插件建议在自己的 `pyproject.toml` 注册入口：

```toml
[project.entry-points."erpilot.business_modules"]
acme-ticketing = "your_company.ticketing:create_module"
```

安装这个包后设置 `ERPILOT_MODULE=acme-ticketing` 即可加载。没有同名入口、存在重复入口或工厂返回类型错误时，Erpilot 会拒绝启动。

新插件统一从 `erpilot_api.plugin` 导入 `BusinessModule`、`BusinessSessionContext`、`Tool`、连接器和对账类型。构造模块时声明 `api_version=1`。未声明版本的现有模块按 v1 加载，当前宿主拒绝其他 API 版本。兼容扩展沿用 v1，破坏性变更需要新的 API 版本。模块自己的 `version` 用于插件发布与会话恢复，和宿主 API 版本各自独立。可在 `plugin check --json` 或 `/api/runtime` 查看 `module.api_version`。

插件仓库可以在 CI 中运行 `erpilot plugin check acme-ticketing --json`。它使用宿主的加载和契约校验逻辑，无需启动模型、API 或 checkpoint 数据库。插件工厂仍需要自己的配置。JSON 中的 `module.contract` 与宿主报告的工具契约摘要来自同一接口，可用于比较发布前后的契约。工具清单包含名称与风险级别。

要验收脚手架的示例查询，可执行：

```powershell
erpilot plugin check acme-ticketing --read-tool get_ticket --args '{"ticket_id":"DEMO-001"}'
```

这个选项以测试会话直接调用普通只读工具，可使用模拟后端。连接真实后端时，命令会发起查询。业务结果由插件自己的测试断言，命令只报告工具是否正常返回。

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
| `health_check()` | 异步检查目标系统依赖；部署 `/readyz` 会调用它 |
| `lifespan` | 可选异步上下文管理器工厂，由 API 在启动时进入、关闭时退出 |
| `suggestions` | 工作台的示例输入 |
| `version` | 模块版本，升级业务契约时同步调整 |
| `loop_config` | 可选 `agent_core.runtime_config.LoopConfig`，设置工具超时、重试和最大推理步数等运行参数 |

可以直接复制 `apps/api/src/erpilot_api/examples/expense.py`，替换 HTTP 地址、请求映射和结果映射。调用方也可以通过 `create_app(business_module=...)` 直接装配。模块模式拥有工具和恢复配置，混用显式 `tools`、旧 ERP provider 或全局 reconciler 会报错。

HTTP 插件可以使用 `HttpConnectorConfig.client(context)` 创建异步客户端，复用连接配置、会话身份头和访问凭据。客户端由调用方关闭，支持传入测试 transport。默认禁止重定向和使用环境代理配置。目标系统要求在 `Authorization` 中直接发送令牌时，可设 `raw_authorization=True`。

需要跨请求复用连接池时，在模块工厂创建 `HttpConnector(config)`，将 `connector.lifespan` 传给 `BusinessModule`，再通过 `await connector.request("GET", "tickets", context=context)` 发起请求。身份头按请求生成，连接器不会保存上游 Cookie，只接受相对路径且不会跟随重定向。离线测试可显式进入连接器生命周期，也可以直接调用，此时客户端在该请求结束后关闭。

`classify_http_error(exc)` 可将 HTTP 客户端异常映射成不含 URL、凭据和响应内容的分类，例如 `timeout`、`transport`、`access_denied`、`rate_limited` 和 `upstream_failure`。HTTP 状态分类适用于 `raise_for_status()` 抛出的异常，只有插件知道某个状态是否有业务含义，例如对账查询何时可以把 404 判为记录不存在。连接器不会自动重试写请求。

工具名、描述、风险等级、可重试标志和参数 schema 应在不同会话与服务重启之间保持稳定。Erpilot 启动时检查声明和示例上下文，也在每次绑定真实会话工具时复查，再由宿主按角色过滤。绑定工厂可以为不同用户创建带不同身份的处理函数，但须返回声明中的完整工具目录。发现工具重复、契约不一致，或拥有写权限的会话缺少对账器时，执行在模型调用前停止，接口返回 `module_contract_mismatch`。所有写工具必须接受 `client_token`。`/api/runtime` 返回模块版本和工具契约摘要，方便部署侧核对版本。

新插件可通过 `loop_config=LoopConfig(max_steps=4, tool_timeout=15)` 配置运行参数。订单示例使用 `BusinessRuntimeFeatures` 声明工作台能力。现有 `service_options` 中的 `loop_config` 和 `business_hooks` 以及已知的 `runtime_features` 布尔字段仍可用。宿主保留认证和写权限状态字段，插件的运行参数由显式扩展点接收。

默认模型执行预算为每条用户请求 16 步。下一条消息使用新的执行预算；插件可通过自己的 `loop_config` 设置其他值。

新会话首次执行时，Erpilot 将实例 ID、模块名称、恢复版本和恢复契约摘要绑定到会话记录及 checkpoint。只有这四项都匹配才能继续该会话或恢复待审批操作。设置稳定的 `ERPILOT_INSTANCE_ID` 来区分实际接入实例，尤其是同一个插件连接不同系统实例、组织或租户时。留空时用模块名称作为实例 ID。切换目标系统实例时，改用独立的实例 ID 和存储，并新建会话。恢复旧任务时使用原实例配置和原存储。

经回归验证的兼容升级可以显式声明 `recovery_version`，保持原版本的绑定值。未声明时沿用发布版本。仅修改工具说明文字时，`recovery_descriptions` 可以为修改过的工具声明旧说明，用于计算恢复摘要。工具参数、风险、重试规则及实例身份仍需匹配。遇到写入语义、权限或对账行为变化，应更新恢复版本并在旧部署处理旧待办。可在 `/api/runtime` 和 `plugin check --json` 核对版本与摘要。

升级前已存在且没有绑定记录的会话可以查看历史。继续对话和恢复执行需要新建会话，或在原版本和原目标系统环境中先处理旧任务。

订单示例支持在意图解析的 `off`、`shadow` 和 `enforce` 之间切换。它的恢复契约沿用这一兼容性，忽略意图解析工具和该模式追加的提示词。第三方模块默认使用完整工具契约摘要。

流式结束事件的 `run_id` 和 `trace` 来自本次执行。审批恢复会生成新的 trace 文件，运行记录指向最近的执行段，先前的文件仍可用于排查。

## 写入和身份边界

写工具设置 `risk` 后通过现有图运行时进入人工确认。运行时把稳定请求号放入 `client_token` 参数，模块把它传到目标系统。仅在目标系统确实支持幂等提交时设置 `retry_safe=True`。

写工具可通过 `approval_presentation=ApprovalPresentation(...)` 声明审批标题、影响说明和可展示字段。字段以 `(参数名, 显示标签)` 声明，例如 `fields=(("purpose", "用途"), ("amount_cents", "金额（分）"))`。框架从已校验的参数读取标量值，随待审批状态保存同一份摘要，工作台即时显示和恢复后显示一致。展示声明不能包含 `client_token`。审批仍绑定原始参数，目标系统的业务约束仍由写工具实现。

插件还可通过 `result_presentation=ResultPresentation(...)` 声明成功结果的标题及字段。宿主使用工具返回的标量字段渲染业务卡，字段缺失或工具返回错误时使用通用详情。

外部插件可在自己的 CI 中导入 `erpilot_api.plugin_testing.assert_write_tool_contract`。传入插件模块、`BusinessSessionContext`、写工具名和两组合法但业务含义不同的参数。两组参数都由宿主生成 `client_token`。例如，报销插件可以用金额分别为 100 分和 200 分的两组参数：

```python
await assert_write_tool_contract(
    module,
    context=BusinessSessionContext("expense-contract"),
    tool_name="submit_expense",
    arguments={"purpose": "travel", "amount_cents": 100},
    conflicting_arguments={"purpose": "travel", "amount_cents": 200},
)
```

检查使用模拟模型和内存检查点，通过框架执行图验证审批前无写入、拒绝后无写入、写入后丢失响应可按原请求号找回结果，以及同一请求号绑定不同参数时返回冲突。恢复过程会检查写处理函数只执行一次。声明 `retry_safe=True` 的工具还会收到一次同参数、同请求号的重放，返回值应等于原结果。**检查会真实调用插件写处理函数，请使用模拟后端或一次性测试库。** 涉及业务记录数量、金额或库存的断言可在插件测试里继续验证。

对账返回 `found` 时附上已提交结果，返回 `conflict` 表示请求号对应的内容不一致。只有能够确定未提交时返回 `absent`。网络超时、服务不可用以及无法确定的提交查询结果应抛出异常，由运行时保持待核对状态。目标系统如存在索引延迟，应根据能确认实际提交结果的记录实现此接口。

模块从服务端配置读取连接凭据。`BusinessSessionContext` 提供 `session_id`、`user_id`、`username` 和 `role`。启用 Erpilot 登录后，后三个字段来自宿主验证过的会话；未启用登录时为 `None`。模块应使用稳定的 `user_id` 映射目标系统的身份、组织与数据权限，不能把 `session_id` 当作用户凭据。目标系统仍须根据服务凭据或用户委托令牌再次鉴权，不能信任客户端直接传入的角色。

旧插件可以继续实现 `for_session` 和 `reconciler`。需要用户级权限或租户隔离的新插件可以只实现两个 context 工厂。账号被停用、会话不存在或不再有效时，宿主不会向模块开放工具，也不会创建写入对账器；operator 仍只获得只读工具，approver 才能获得写工具。

HTTP 插件可以复用 `HttpConnectorConfig`。它统一解析 `<PREFIX>_URL`、`<PREFIX>_SERVICE_TOKEN`、`<PREFIX>_TIMEOUT` 和 `<PREFIX>_VERIFY_TLS`，生成服务端 `Authorization` 以及经过校验的会话、用户和角色请求头。配置对象的字符串表示和公开摘要不会包含令牌；携带服务凭据访问非本机地址时强制使用 HTTPS。用户身份请求头只说明 Erpilot 已验证的操作者，目标系统仍应验证服务令牌，并将用户 ID 映射到自己的账号、组织和权限。

报销示例后端可通过 `ERPILOT_EXPENSE_BACKEND_TOKEN` 启用 Bearer 校验，Erpilot 侧把相同秘密配置到 `ERPILOT_EXPENSE_SERVICE_TOKEN`。本机无令牌模式只用于快速联调。部署就绪检查会在数据库和 checkpoint 之外调用模块 `health_check`；目标系统无法连接、超时或凭据错误时 `/readyz` 返回 503，但 `/healthz` 仍只表示 Erpilot 进程存活。

测试 ERP 仅用于本地联调，没有接入企业认证。`expense_backend.py` 使用操作者 ID 与会话 ID 共同划分示例数据，并以同一 SQLite 事务保存申请和请求号，演示重复提交、参数冲突、操作者隔离及重启后的结果查询。

## 当前范围与后续迁移

当前已支持 Python 入口插件和 `module:factory` 两种加载方式、启动契约校验、带登录身份的会话工具与对账器绑定、失效账号安全关闭、HTTP 连接配置与服务凭据、目标系统健康检查、业务提示词、通用审批与恢复、模块工作台提示，以及订单示例的显式装配。订单专用路由由订单模块装配。

订单示例通过 `order_module.py` 声明自己的路由、运行特性、状态增强和语义解析能力。工作台按运行特性加载订单草稿组件，第三方模块复用通用审批和结果卡。独立安装宿主时，订单依赖通过 `erpilot-api[order-example]` 安装。工作区全包同步及容器仍包含参考业务模块。

单 API 执行进程约束继续适用。企业 SSO、租户隔离、多 worker 执行协调和动态插件安装需要独立实现与验证。

## 验证

工单脚手架可通过正式 API 启动入口执行示例查询，使用固定数据和脚本化模型测试。接入真实 CRM、OA 或工单后端时，可在独立插件仓库实现和验证对应适配器。

[Roundup 独立插件](../examples/roundup-plugin/README.md)可在本地启动真实工单系统，验收基于操作者身份的查询、创建审批和丢失响应后的提交核对。

```powershell
uv run python -m pytest apps/api/tests/test_business_modules.py apps/api/tests/test_graph_api.py apps/api/tests/test_live_defaults.py apps/cli/tests/test_plugin_scaffold.py
npm --prefix apps/web run build
```

模块测试通过 HTTP ASGI transport 连接独立测试 ERP，覆盖批准与拒绝、待审批时重建宿主、重复提交、内容冲突、会话隔离、远端错误以及正式启动入口。模型使用固定测试响应，方便重复执行。

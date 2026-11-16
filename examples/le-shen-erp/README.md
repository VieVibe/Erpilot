# 乐深 ERP 只读接入验证

该适配器注册四个只读工具，由模型按问题选择工具和填入参数。所有接口沿用乐深权限校验。

| 工具 | 乐深 GET 接口 | 支持的参数 |
| --- | --- | --- |
| `list_leshen_categories` | `/api/erp/category/page` | 类别名称、页码、每页条数 |
| `list_leshen_products` | `/api/erp/product/page` | 商品名称、类别 ID、页码、每页条数 |
| `list_leshen_warehouses` | `/api/erp/warehouse/page` | 仓库名称、页码、每页条数 |
| `list_leshen_stock` | `/api/erp/stock/page` | 商品名称、仓库名称、页码、每页条数 |

页码从 1 开始，每页 1–10 条。缺省为第 1 页、5 条。按类别名称查商品时，可以先查类别获取 ID。

在此目录的 `.env.local` 中设置测试账号的 ERP 登录令牌，文件已由仓库忽略。
也可从进程环境传入。为 ERP 测试账号授予类别、商品、仓库和库存的查询权限即可。

```dotenv
LESHEN_ERP_URL=http://127.0.0.1:8082
LESHEN_ERP_SERVICE_TOKEN=
LESHEN_ERP_HOST_USER=leshen-operator
LESHEN_DEMO_PASSWORD=
```

从 Erpilot 根目录运行工作台。脚本从项目根目录的 `.env` 读取 Erpilot 模型配置。
配置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，使用支持工具调用的模型。
对话、工具定义和经过字段筛选的查询结果会发送到配置的模型服务。

```powershell
npm --prefix apps/web run build
uv run --no-sync --package erpilot-api python examples/le-shen-erp/serve_demo.py
```

打开 [工作台](http://127.0.0.1:8769/)，使用 `LESHEN_ERP_HOST_USER` 和
`LESHEN_DEMO_PASSWORD` 登录。可以试问「查询第 2 页商品类别，每页 2 条」、
「列出前 3 个商品及其编码」或「查询某个仓库的前 2 条库存」。

以下验收命令使用真实 ERP HTTP 接口及独立的脚本模型，检查四个工具的分页、
名称过滤、账号映射和乐深令牌校验。

```powershell
uv run --no-sync --package erpilot-api python examples/le-shen-erp/verify.py
```

要在现有 Erpilot 工作台中加载，请将插件目录加入 `PYTHONPATH`，设置
`ERPILOT_MODULE=le_shen_erp:create_module` 和 `ERPILOT_AUTH=1`，并在 Erpilot
服务进程中配置上述变量。用独立 RunStore 和 checkpoint 启动实例，
在其 RunStore 创建与 `LESHEN_ERP_HOST_USER` 对应的 operator 账号。
完整工作台启动仍使用 Erpilot 的模型与 checkpoint 配置。

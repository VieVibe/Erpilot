"""Generate and validate standalone business modules."""

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated

import typer
from erpilot_api.modules import (
    BusinessModule,
    BusinessSessionContext,
    PluginApiVersionError,
    load_module,
)
from pydantic import ValidationError

app = typer.Typer(help="创建和检查业务模块插件", no_args_is_help=True)

_PROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "erpilot-plugin-@NAME@"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["erpilot-api>=0.1.0", "pydantic>=2"]

[project.entry-points."erpilot.business_modules"]
@NAME@ = "@PACKAGE@:create_module"

[tool.hatch.build.targets.wheel]
packages = ["src/@PACKAGE@"]
"""

_MODULE = '''\
"""@NAME@ plugin starter."""

from erpilot_api.plugin import BusinessModule, BusinessSessionContext, Tool
from pydantic import BaseModel, ConfigDict, Field


class TicketQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str = Field(min_length=1, max_length=64, pattern=r"\\S")


def tools_for(context: BusinessSessionContext) -> list[Tool]:
    async def get_ticket(args: TicketQuery) -> dict:
        # Replace this sample with a target-system API call. Use context.user_id
        # to map the signed-in user to the target system's permissions if needed.
        if args.ticket_id != "DEMO-001":
            return {"status": "not_found", "ticket_id": args.ticket_id}
        return {
            "status": "found", "ticket_id": args.ticket_id,
            "subject": "示例工单", "ticket_status": "处理中",
        }

    return [Tool("get_ticket", "按编号查询示例工单", TicketQuery, get_ticket)]


def create_module() -> BusinessModule:
    return BusinessModule(
        name="@NAME@",
        title="@NAME@",
        version="0.1.0",
        api_version=1,
        instructions="使用 get_ticket 按工单编号查询，只根据工具结果回答。",
        tools=tools_for(BusinessSessionContext("discovery")),
        for_context=tools_for,
        suggestions=("查询工单 DEMO-001",),
    )
'''

_README = """\
# @NAME@

Erpilot 只读插件。示例工具可以查询固定工单 `DEMO-001`。

在 Erpilot 仓库根目录执行以下 PowerShell 命令。将安装路径替换为插件的实际目录。

```powershell
uv sync --all-packages
uv pip install --no-deps -e ./@NAME@
uv run --no-sync --package erpilot-cli erpilot plugin check @NAME@
$env:ERPILOT_MODULE = '@NAME@'
uv run --no-sync --package erpilot-api python -m erpilot_api
```

API 启动需要 Erpilot 的模型和 checkpoint 配置。打开工作台后输入“查询工单 DEMO-001”。
在 `src/@PACKAGE@/__init__.py` 中替换 `get_ticket`，将参数和结果映射到自己的业务系统。
需要 HTTP 地址或服务令牌时，可复用 `erpilot_api.connectors.HttpConnectorConfig`。
需要写入时，请查看 Erpilot 的 `docs/business-modules.md`，接入人工确认、幂等键和提交结果核对。
"""


@app.command("init")
def init_plugin(
    name: Annotated[str, typer.Argument(help="插件入口名称，例如 acme-ticketing")],
    output: Annotated[Path, typer.Option("--output", "-o", help="插件的父目录")] = Path("."),
) -> None:
    """在指定目录下创建可安装的只读插件。"""
    if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name) is None or len(name) > 50:
        raise typer.BadParameter("名称须以小写字母开头，使用小写字母、数字和单连字符")
    if name == "expense-example":
        raise typer.BadParameter("该名称已用于内置报销示例")

    target = output / name
    package = "erpilot_plugin_" + name.replace("-", "_")
    try:
        target.mkdir(parents=True)
        package_dir = target / "src" / package
        package_dir.mkdir(parents=True)
        for path, template in (
            (target / "pyproject.toml", _PROJECT),
            (target / "README.md", _README),
            (package_dir / "__init__.py", _MODULE),
            (target / ".gitignore", ".env\n.venv/\n__pycache__/\n*.egg-info/\n"),
        ):
            path.write_text(
                template.replace("@NAME@", name).replace("@PACKAGE@", package),
                encoding="utf-8",
            )
    except OSError as exc:
        typer.echo(f"无法创建插件 {target}：{exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"已创建 {target.resolve()}")
    typer.echo(f"接入步骤见 README.md，插件入口为 {name}。")


async def _try_read_tool(module: BusinessModule, name: str, arguments: str | None) -> None:
    context = BusinessSessionContext("erpilot-plugin-check")
    try:
        tools = module.tools_for(context)
    except Exception as error:
        typer.echo(f"测试会话工具绑定失败（{type(error).__name__}）。", err=True)
        raise typer.Exit(1) from None
    selected = next((tool for tool in tools if tool.name == name), None)
    if selected is None or any((
        selected.risk is not None,
        selected.call_validator,
        selected.required_read,
        selected.stage_policy,
        selected.validated_handler,
    )):
        typer.echo("只能调用不依赖会话策略或执行前校验的只读工具。", err=True)
        raise typer.Exit(1)
    try:
        params = selected.params_model.model_validate_json(arguments or "{}")
    except ValidationError as error:
        for issue in error.errors(include_input=False, include_context=False):
            field = ".".join(map(str, issue["loc"])) or "args"
            typer.echo(f"{field}: {issue['type']}", err=True)
        raise typer.Exit(1) from None
    try:
        await selected.handler(params)
    except Exception as error:
        typer.echo(f"只读工具调用失败（{type(error).__name__}）。", err=True)
        raise typer.Exit(1) from None


async def _check_plugin(
    spec: str, read_tool: str | None, arguments: str | None, *, json_output: bool,
) -> None:
    try:
        module = await load_module(spec)
    except PluginApiVersionError:
        typer.echo("插件 API 版本不受当前宿主支持，请检查插件与 Erpilot 的版本。", err=True)
        raise typer.Exit(1) from None
    except Exception as error:
        # Plugin configuration errors can contain credentials.
        typer.echo(f"插件加载或契约校验失败（{type(error).__name__}）。", err=True)
        raise typer.Exit(1) from None

    if read_tool is not None:
        await _try_read_tool(module, read_tool, arguments)
    if json_output:
        typer.echo(json.dumps({
            "module": module.manifest(),
            "tools": [{"name": tool.name, "risk": tool.risk} for tool in module.tools],
            "read_tool": read_tool,
        }, ensure_ascii=False))
        return

    typer.echo(f"{module.name} {module.version}")
    for tool in module.tools:
        typer.echo(f"{tool.name} ({'read' if tool.risk is None else 'write'})")
    if read_tool is not None:
        typer.echo(f"只读工具已返回：{read_tool}")


@app.command("check")
def check_plugin(
    spec: Annotated[str, typer.Argument(help="已安装的插件名或 module:factory")],
    read_tool: Annotated[str | None, typer.Option(help="显式调用的只读工具")] = None,
    arguments: Annotated[str | None, typer.Option("--args", help="只读工具的 JSON 参数")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="输出模块契约和工具清单")] = False,
):
    """校验插件契约。显式调用工具时可能访问目标系统。"""
    if read_tool is None and arguments is not None:
        raise typer.BadParameter("--args 必须与 --read-tool 一起使用")
    asyncio.run(_check_plugin(spec, read_tool, arguments, json_output=json_output))

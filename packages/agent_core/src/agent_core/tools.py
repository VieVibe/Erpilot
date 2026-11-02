"""工具协议：用 Pydantic 定义工具签名，自动生成注入请求的 JSON Schema。

一个工具 = 名称 + 描述 + 参数模型（Pydantic）+ 异步处理函数。
handler 收到的是**已通过 Pydantic 校验**的参数实例，业务侧不用再解析 JSON。
M3 起真实 ERP 工具走 mcp_erp（FastMCP），本模块是 agent 侧的本地协议。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agent_core.response_contract import ResponseContract


class ToolConstraintError(ValueError):
    """Trusted application preflight rejection with a stable, serializable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _strip_titles(node: Any) -> Any:
    """递归去掉 JSON Schema 里的 title 键——对注入请求是纯噪音。"""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    params_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[object]]
    # 风险等级（ADR-0005）：None = 只读放行；写工具由 mcp_erp 标注
    # batch_confirm / single_confirm，审批门（approval.guarded）据此拦截
    risk: str | None = None
    retry_safe: bool = False  # 写工具必须由适配层提供稳定幂等键，才能自动重试
    # Graph composition can execute the original handler only after its durable interrupt.
    approved_handler: Callable[[BaseModel], Awaitable[object]] | None = None
    approval_gate: object | None = None
    response_contract: ResponseContract | None = None
    # Application-owned validation against trusted conversation state.
    # Returns JSON evidence; ValueError rejects the call before execution or approval.
    call_validator: Callable[[dict, list[dict], list[dict]], dict] | None = None
    # Receives application validation evidence outside the model-visible schema.
    validated_handler: Callable[[BaseModel, dict], Awaitable[object]] | None = None
    # Application-selected read prerequisites, evaluated against this turn's real history.
    # Returns this tool's arguments, or None. Never installed on an approval tool.
    required_read: Callable[[list[dict], list[dict]], dict | None] | None = None
    # Trusted application mapping: a known business failure ends the turn.
    # It cannot be supplied or extended by model/tool output.
    terminal_errors: dict[str, str] | None = None
    # Trusted renderer for a human-selected, approved action without model planning.
    direct_response: Callable[[dict], str | None] | None = None
    # Optional application policy; also checked when a model bypasses visibility.
    stage_policy: Callable[[list[dict], list[dict]], bool] | None = None

    def __post_init__(self):
        if self.validated_handler is not None and self.risk is not None:
            raise ValueError("validated handlers cannot bypass approval handlers")
        if self.required_read is not None and self.risk is not None:
            raise ValueError("required reads cannot bypass approval handlers")

    def openai_schema(self) -> dict[str, Any]:
        """OpenAI tools 参数格式：{"type": "function", "function": {...}}。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _strip_titles(self.params_model.model_json_schema()),
            },
        }


def tool(
    *, name: str, description: str, params: type[BaseModel], risk: str | None = None
) -> Callable[[Callable[[BaseModel], Awaitable[object]]], Tool]:
    """装饰器：把 async 函数注册成 Tool，调用时以 params 模型校验入参。"""

    def decorator(fn: Callable[[BaseModel], Awaitable[object]]) -> Tool:
        return Tool(name=name, description=description, params_model=params, handler=fn, risk=risk)

    return decorator

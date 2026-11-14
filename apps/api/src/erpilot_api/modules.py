"""Application-owned business modules loaded once at startup."""

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from importlib.metadata import entry_points
from typing import Any

from agent_core.recovery import MutationReconciler
from agent_core.tools import Tool

ENTRY_POINT_GROUP = "erpilot.business_modules"


@dataclass(frozen=True)
class BusinessSessionContext:
    """Host-verified identity available while binding a module to a session."""

    session_id: str
    user_id: str | None = None
    username: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class BusinessModule:
    """Bind tools and recovery to a trusted session, with optional example UI hints.

    Factories must return stable names and schemas across sessions and restarts.
    Session IDs are host-owned conversation IDs, not ERP user credentials. New
    modules should prefer the context factories when ERP authorization depends
    on the signed-in user; the session-only factories remain compatible.
    """

    name: str
    title: str
    instructions: str | None
    tools: Sequence[Tool]
    for_session: Callable[[str], Sequence[Tool]] | None = None
    reconciler: Callable[[str], MutationReconciler | None] = lambda session_id: None
    suggestions: tuple[str, ...] = ()
    version: str = "1"
    data_source: str = "custom"
    approval_gate: object | None = None
    service_options: Mapping[str, Any] | None = None
    runtime_features: Mapping[str, bool] | None = None
    install_routes: Callable[[Any, Any, Callable[..., Any]], None] | None = None
    semantic_analyzer: object | None = None
    semantic_tool_name: str | None = None
    for_context: Callable[[BusinessSessionContext], Sequence[Tool]] | None = None
    reconciler_for_context: (
        Callable[[BusinessSessionContext], MutationReconciler | None] | None
    ) = None
    connector_summary: Mapping[str, object] | None = None
    health_check: Callable[[], Awaitable[None]] | None = None

    def __post_init__(self):
        names = [tool.name for tool in self.tools]
        if not self.name.strip() or not self.title.strip():
            raise ValueError("business module name and title are required")
        if not self.version.strip():
            raise ValueError("business module version is required")
        if len(names) != len(set(names)):
            raise ValueError("business module tool names must be unique")
        if self.instructions is None and self.data_source != "mcp":
            raise ValueError("custom business modules must provide instructions")
        if self.data_source not in {"custom", "mcp", "demo"}:
            raise ValueError("business module data_source is invalid")
        if self.for_session is None and self.for_context is None:
            raise ValueError("business module must provide a session or context tool factory")
        if any(not suggestion.strip() or len(suggestion) > 32000
               for suggestion in self.suggestions):
            raise ValueError("business module suggestions must contain 1 to 32000 characters")

    def tools_for(self, context: BusinessSessionContext) -> Sequence[Tool]:
        if self.for_context is not None:
            return self.for_context(context)
        if self.for_session is None:  # Guarded by __post_init__; narrows the type.
            raise RuntimeError("business module has no tool factory")
        return self.for_session(context.session_id)

    def reconciler_for(self, context: BusinessSessionContext) -> MutationReconciler | None:
        if self.reconciler_for_context is not None:
            return self.reconciler_for_context(context)
        return self.reconciler(context.session_id)

    def manifest(self) -> dict[str, str]:
        contract = json.dumps(
            [_tool_contract(tool) for tool in self.tools],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "contract": sha256(contract.encode()).hexdigest(),
        }


def _tool_contract(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "risk": tool.risk,
        "retry_safe": tool.retry_safe,
        "parameters": tool.params_model.model_json_schema(),
    }


def validate_module(module: BusinessModule) -> BusinessModule:
    """Fail startup when session binding would invalidate persisted tool calls."""
    catalog = {tool.name: _tool_contract(tool) for tool in module.tools}
    validation_context = BusinessSessionContext(
        session_id="__erpilot_contract_validation__",
        user_id="__erpilot_contract_validation__",
        username="contract-validation",
        role="approver",
    )
    bound_tools = list(module.tools_for(validation_context))
    bound = {tool.name: _tool_contract(tool) for tool in bound_tools}
    if len(bound) != len(bound_tools) or bound != catalog:
        raise ValueError(
            "business module session factory must preserve tool names, descriptions and schemas"
        )
    write_tools = [tool for tool in module.tools if tool.risk is not None]
    for tool in write_tools:
        if "client_token" not in tool.params_model.model_fields:
            raise ValueError(f"write tool {tool.name!r} must accept client_token")
    if write_tools:
        reconciler = module.reconciler_for(validation_context)
        if reconciler is None or not callable(getattr(reconciler, "lookup", None)):
            raise ValueError("business modules with write tools must provide a reconciler")
    return module


async def load_module(spec: str) -> BusinessModule:
    """Load module:factory or an installed erpilot.business_modules entry point."""
    if ":" in spec:
        module_name, separator, factory_name = spec.partition(":")
        if not separator or not module_name or not factory_name or ":" in factory_name:
            raise ValueError("ERPILOT_MODULE must be a plugin name or Python module:factory")
        factory = getattr(import_module(module_name), factory_name)
    else:
        matches = [point for point in entry_points(group=ENTRY_POINT_GROUP)
                   if point.name == spec]
        if not matches:
            raise ValueError(f"business module plugin {spec!r} is not installed")
        if len(matches) > 1:
            raise ValueError(f"business module plugin {spec!r} is ambiguous")
        factory = matches[0].load()
    result = factory()
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, BusinessModule):
        raise TypeError("business module factory must return BusinessModule")
    return validate_module(result)

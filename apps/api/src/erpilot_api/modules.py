"""Application-owned business modules loaded once at startup."""

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, fields
from hashlib import sha256
from importlib import import_module
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, TypedDict

from agent_core.recovery import MutationReconciler
from agent_core.runtime_config import LoopConfig
from agent_core.tools import Tool

if TYPE_CHECKING:
    from erpilot_api.order_module import OrderServiceHooks

ENTRY_POINT_GROUP = "erpilot.business_modules"
PLUGIN_API_VERSION = 1


class ModuleBindingError(ValueError):
    """The tools or reconciler bound to a session violate the module declaration."""


class PluginApiVersionError(ModuleBindingError):
    """The plugin requires an unsupported host API version."""


class LegacyServiceOptions(TypedDict, total=False):
    loop_config: LoopConfig
    business_hooks: "OrderServiceHooks"


@dataclass(frozen=True)
class BusinessRuntimeFeatures:
    drafts_enabled: bool = False
    intent_preview_enabled: bool | None = None
    intent_recovery_enabled: bool | None = None
    business_approvals_enabled: bool = False

    def __post_init__(self):
        for name in ("drafts_enabled", "business_approvals_enabled"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")
        for name in ("intent_preview_enabled", "intent_recovery_enabled"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{name} must be a boolean when set")


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
    New modules should use the context factories to map signed-in users to
    target-system permissions. The host owns session IDs and keeps the
    session-only factories for existing plugins.
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
    service_options: LegacyServiceOptions | None = None
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
    recovery_contract: str | None = None
    loop_config: LoopConfig | None = None
    features: BusinessRuntimeFeatures | None = None
    order_hooks: "OrderServiceHooks | None" = None
    lifespan: Callable[[], AbstractAsyncContextManager[None]] | None = None
    api_version: int = PLUGIN_API_VERSION
    recovery_version: str | None = None
    recovery_descriptions: Mapping[str, str] = field(default_factory=dict)
    _catalog: dict[str, dict[str, Any]] = field(init=False, repr=False, compare=False)
    _contract: str = field(init=False, repr=False, compare=False)
    _recovery_digest: str = field(init=False, repr=False, compare=False)
    runtime_loop_config: LoopConfig | None = field(init=False, repr=False, compare=False)
    runtime_order_hooks: "OrderServiceHooks | None" = field(init=False, repr=False, compare=False)
    runtime_capabilities: BusinessRuntimeFeatures = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        tools = tuple(self.tools)
        if not self.name.strip() or not self.title.strip():
            raise ValueError("business module name and title are required")
        if not self.version.strip():
            raise ValueError("business module version is required")
        if self.recovery_version is not None and not self.recovery_version.strip():
            raise ValueError("recovery_version must not be blank")
        if self.instructions is None and self.data_source != "mcp":
            raise ValueError("custom business modules must provide instructions")
        if self.data_source not in {"custom", "mcp", "demo"}:
            raise ValueError("business module data_source is invalid")
        if self.for_session is None and self.for_context is None:
            raise ValueError("business module must provide a session or context tool factory")
        if any(not suggestion.strip() or len(suggestion) > 32000
               for suggestion in self.suggestions):
            raise ValueError("business module suggestions must contain 1 to 32000 characters")

        if any(not isinstance(tool, Tool) for tool in tools):
            raise TypeError("business module catalog must contain Tool objects")
        catalog = {tool.name: _tool_contract(tool) for tool in tools}
        if len(catalog) != len(tools):
            raise ValueError("business module tool names must be unique")
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "_catalog", catalog)
        object.__setattr__(self, "_contract", _contract_digest(list(catalog.values())))
        previous_descriptions = self.recovery_descriptions
        if not isinstance(previous_descriptions, Mapping) or any(
            name not in catalog or not isinstance(description, str)
            for name, description in previous_descriptions.items()
        ):
            raise ValueError("recovery_descriptions must map declared tools to prior descriptions")
        if previous_descriptions and self.recovery_contract is not None:
            raise ValueError("configure recovery descriptions or a recovery contract")
        object.__setattr__(self, "_recovery_digest", self.recovery_contract or _contract_digest([
            {**entry, "description": previous_descriptions.get(name, entry["description"])}
            for name, entry in catalog.items()
        ]))

        if self.service_options is not None and not isinstance(self.service_options, Mapping):
            raise TypeError("service_options must be a mapping")
        options = self.service_options or {}
        unknown = options.keys() - LegacyServiceOptions.__annotations__.keys()
        if unknown:
            raise ValueError(f"unsupported service_options: {', '.join(sorted(unknown))}")
        if self.loop_config is not None and "loop_config" in options:
            raise ValueError("configure loop_config once")
        if self.order_hooks is not None and "business_hooks" in options:
            raise ValueError("configure order hooks once")
        config = self.loop_config if self.loop_config is not None else options.get("loop_config")
        if config is not None and not isinstance(config, LoopConfig):
            raise TypeError("loop_config must be a LoopConfig")
        object.__setattr__(self, "runtime_loop_config", config)
        hooks = self.order_hooks if self.order_hooks is not None else options.get("business_hooks")
        object.__setattr__(self, "runtime_order_hooks", hooks)

        if self.features is not None and self.runtime_features is not None:
            raise ValueError("configure runtime features once")
        if self.features is not None and not isinstance(self.features, BusinessRuntimeFeatures):
            raise TypeError("features must be BusinessRuntimeFeatures")
        if self.runtime_features is not None:
            if not isinstance(self.runtime_features, Mapping):
                raise TypeError("runtime_features must be a mapping")
            known = {feature.name for feature in fields(BusinessRuntimeFeatures)}
            unknown = self.runtime_features.keys() - known
            if unknown:
                raise ValueError(f"unsupported runtime_features: {', '.join(sorted(unknown))}")
            if any(type(value) is not bool for value in self.runtime_features.values()):
                raise TypeError("runtime_features values must be booleans")

        features = self.features or BusinessRuntimeFeatures(**(self.runtime_features or {}))
        object.__setattr__(self, "runtime_capabilities", features)

    def tools_for(self, context: BusinessSessionContext) -> Sequence[Tool]:
        if self.for_context is not None:
            supplied = self.for_context(context)
        elif self.for_session is not None:
            supplied = self.for_session(context.session_id)
        else:
            raise RuntimeError("business module has no tool factory")
        try:
            tools = list(supplied)
        except TypeError as exc:
            raise ModuleBindingError("business module session factory must return tools") from exc
        if any(not isinstance(tool, Tool) for tool in tools):
            raise ModuleBindingError("business module session factory must return Tool objects")
        actual = {tool.name: _tool_contract(tool) for tool in tools}
        if len(actual) != len(tools) or actual != self._catalog:
            raise ModuleBindingError(
                "business module session factory must preserve tool names, descriptions, "
                "risk, retry policy and schemas"
            )
        return tools

    def reconciler_for(
        self, context: BusinessSessionContext, *, required: bool = False,
    ) -> MutationReconciler | None:
        if self.reconciler_for_context is not None:
            reconciler = self.reconciler_for_context(context)
        else:
            reconciler = self.reconciler(context.session_id)
        if (required or reconciler is not None) and not callable(
            getattr(reconciler, "lookup", None)
        ):
            raise ModuleBindingError("business modules with write tools must provide a reconciler")
        return reconciler

    def manifest(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "recovery_version": self.recovery_version or self.version,
            "recovery_contract": self._recovery_digest,
            "contract": self._contract,
            "api_version": self.api_version,
        }


def tool_contract_digest(tools: Sequence[Tool]) -> str:
    return _contract_digest([_tool_contract(tool) for tool in tools])


def _contract_digest(contracts: list[dict[str, Any]]) -> str:
    contract = json.dumps(
        contracts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(contract.encode()).hexdigest()


def _tool_contract(tool: Tool) -> dict[str, Any]:
    try:
        parameters = tool.params_model.model_json_schema()
    except Exception as exc:
        raise ModuleBindingError(f"tool {tool.name!r} has an invalid parameter schema") from exc
    if tool.approval_presentation is not None and (
        tool.risk is None or any(
            name not in tool.params_model.model_fields
            for name, _ in tool.approval_presentation.fields
        )
    ):
        raise ModuleBindingError("approval fields must reference declared write tool arguments")
    return {
        "name": tool.name,
        "description": tool.description,
        "risk": tool.risk,
        "retry_safe": tool.retry_safe,
        "parameters": parameters,
    }


def validate_module(module: BusinessModule) -> BusinessModule:
    """Fail startup when session binding would invalidate persisted tool calls."""
    if type(module.api_version) is not int or module.api_version != PLUGIN_API_VERSION:
        raise PluginApiVersionError(
            f"unsupported plugin API version: this host supports {PLUGIN_API_VERSION}"
        )
    validation_context = BusinessSessionContext(
        session_id="__erpilot_contract_validation__",
        user_id="__erpilot_contract_validation__",
        username="contract-validation",
        role="approver",
    )
    module.tools_for(validation_context)
    write_tools = [tool for tool in module.tools if tool.risk is not None]
    for tool in write_tools:
        if "client_token" not in tool.params_model.model_fields:
            raise ValueError(f"write tool {tool.name!r} must accept client_token")
    if write_tools:
        module.reconciler_for(validation_context, required=True)
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

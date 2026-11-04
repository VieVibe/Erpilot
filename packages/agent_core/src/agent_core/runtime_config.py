"""Shared limits and retry policy for the graph runtime."""

import math
from dataclasses import dataclass, field

from agent_core.context import ContextPolicy
from agent_core.context_view import ContextView


@dataclass(frozen=True, slots=True)
class ToolRetryPolicy:
    """Retry transient tool failures; deterministic input errors are never retried."""

    retries: int = 1
    backoff: float = 0.5
    retry_on: frozenset[str] = frozenset({"timeout", "execution"})


@dataclass(frozen=True, slots=True)
class LoopConfig:
    max_steps: int = 8
    tool_timeout: float = 30.0
    retry: ToolRetryPolicy = field(default_factory=ToolRetryPolicy)
    context: ContextPolicy = field(default_factory=ContextPolicy)
    context_view: ContextView | None = None
    tool_timeouts: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        for name, seconds in self.tool_timeouts.items():
            if (not isinstance(name, str) or not name.strip() or isinstance(seconds, bool)
                    or not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                    or seconds <= 0):
                raise ValueError("per-tool timeouts require a name and finite positive seconds")

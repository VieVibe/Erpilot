"""Trusted application callbacks for evidence-bound, terminal responses.

Callbacks are installed by the tool adapter, never selected from tool output.
Only their JSON identifiers/results enter checkpoints.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResponseContract:
    key: str
    evidence_tool: str
    finalize_tool: str
    render: Callable[[dict | None, list[dict], list[dict]], dict[str, Any]]
    blocked_response: Callable[[list[dict], list[dict]], dict[str, Any] | None] | None = None

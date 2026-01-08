"""Domain models for run state and pending actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class PendingAction:
    kind: Literal["approval", "client_tool"]
    tool_call_id: str
    tool_name: str
    tool_input: dict[str, Any] | None
    invocation_id: str
    adk_confirmation_call_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class RunState:
    run_id: str
    session_id: str
    invocation_id: str | None = None
    pending_approvals: dict[str, PendingAction] = field(default_factory=dict)
    pending_client_tools: dict[str, PendingAction] = field(default_factory=dict)

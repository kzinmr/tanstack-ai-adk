"""
Port definition for run state storage.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.models import PendingAction, RunState


class RunStorePort(Protocol):
    def get_or_create(self, run_id: str) -> RunState: ...

    def get(self, run_id: str) -> RunState | None: ...

    def set_invocation_id(self, run_id: str, invocation_id: str) -> None: ...

    def add_pending_approval(self, run_id: str, action: PendingAction) -> None: ...

    def add_pending_client_tool(self, run_id: str, action: PendingAction) -> None: ...

    def get_pending_approval(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None: ...

    def get_pending_client_tool(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None: ...

    def pop_pending_approval(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None: ...

    def pop_pending_client_tool(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None: ...

    def has_pending(self, run_id: str) -> bool: ...


__all__ = ["RunState", "RunStorePort", "PendingAction"]

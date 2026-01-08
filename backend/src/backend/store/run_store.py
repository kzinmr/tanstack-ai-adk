"""
Run store backends and factory.
"""

from __future__ import annotations

from ..domain.models import PendingAction, RunState
from ..ports import RunStorePort
from ..settings import get_settings


class InMemoryRunStore(RunStorePort):
    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}

    def get_or_create(self, run_id: str) -> RunState:
        state = self._runs.get(run_id)
        if state is None:
            state = RunState(run_id=run_id, session_id=run_id)
            self._runs[run_id] = state
        return state

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def set_invocation_id(self, run_id: str, invocation_id: str) -> None:
        state = self.get_or_create(run_id)
        state.invocation_id = invocation_id

    def add_pending_approval(self, run_id: str, action: PendingAction) -> None:
        state = self.get_or_create(run_id)
        state.pending_approvals[action.tool_call_id] = action

    def add_pending_client_tool(self, run_id: str, action: PendingAction) -> None:
        state = self.get_or_create(run_id)
        state.pending_client_tools[action.tool_call_id] = action

    def get_pending_approval(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None:
        state = self.get(run_id)
        if state is None:
            return None
        return state.pending_approvals.get(tool_call_id)

    def get_pending_client_tool(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None:
        state = self.get(run_id)
        if state is None:
            return None
        return state.pending_client_tools.get(tool_call_id)

    def pop_pending_approval(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None:
        state = self.get(run_id)
        if state is None:
            return None
        return state.pending_approvals.pop(tool_call_id, None)

    def pop_pending_client_tool(
        self, run_id: str, tool_call_id: str
    ) -> PendingAction | None:
        state = self.get(run_id)
        if state is None:
            return None
        return state.pending_client_tools.pop(tool_call_id, None)

    def has_pending(self, run_id: str) -> bool:
        state = self.get(run_id)
        if state is None:
            return False
        return bool(state.pending_approvals or state.pending_client_tools)


_run_store: RunStorePort | None = None


def get_run_store() -> RunStorePort:
    """Get or create the configured run store."""
    global _run_store
    if _run_store is not None:
        return _run_store

    settings = get_settings()
    backend = settings.run_store_backend
    if backend == "memory":
        _run_store = InMemoryRunStore()
        return _run_store

    raise RuntimeError(
        f"Unsupported run store backend: {backend}. Only 'memory' is supported."
    )

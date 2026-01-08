"""In-memory continuation hub for approval/tool result callbacks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class ContinuationHub:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = defaultdict(
            asyncio.Queue
        )

    async def wait(self, run_id: str) -> dict[str, Any]:
        queue = self._queues[run_id]
        return await queue.get()

    def push(self, run_id: str, payload: dict[str, Any]) -> None:
        queue = self._queues[run_id]
        queue.put_nowait(payload)

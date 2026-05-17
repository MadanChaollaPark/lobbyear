"""In-process registry of active LobbyEar runs.

Each run has:
  - an asyncio.Queue of event dicts (reasoning text, tool_use, tool_result)
  - a rolling snapshot of the briefing-in-progress
  - a status (running, finished, error)

The agent loop pushes events into the queue. SSE endpoints drain it.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


_SENTINEL: dict[str, Any] = {"kind": "_done"}


@dataclass
class Run:
    id: str
    status: str = "pending"        # pending | running | finished | error
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    briefing_snapshot: dict[str, Any] | None = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    def push(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)

    def finish(self, briefing: dict[str, Any] | None = None) -> None:
        self.status = "finished"
        self.briefing_snapshot = briefing
        self.queue.put_nowait(_SENTINEL)

    def fail(self, message: str) -> None:
        self.status = "error"
        self.error = message
        self.queue.put_nowait(_SENTINEL)


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = asyncio.Lock()

    def create(self) -> Run:
        run_id = uuid.uuid4().hex[:12]
        run = Run(id=run_id)
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r.id,
                "status": r.status,
                "error": r.error,
                "event_count": len(r.events),
            }
            for r in self._runs.values()
        ]


# One process-wide registry. Demo-grade; restart wipes it.
REGISTRY = RunRegistry()
SENTINEL = _SENTINEL

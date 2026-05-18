"""GET /runs/{id}/events — Server-Sent Events stream of agent activity.

Each event drained from the run's queue is serialized as JSON and emitted
as a single SSE message. A late-joining client receives any events the
agent has already produced (the registry keeps the rolling log), then
streams live until the agent finalizes.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from .registry import REGISTRY


router = APIRouter()


async def _event_stream(run_id: str) -> AsyncIterator[dict[str, str]]:
    run = REGISTRY.get(run_id)
    if run is None:
        return

    # Each client reads from the append-only event log. A single shared queue
    # would make one browser consume events that other browsers also need.
    next_event = 0
    last_heartbeat = asyncio.get_running_loop().time()
    while True:
        while next_event < len(run.events):
            event = run.events[next_event]
            next_event += 1
            yield {"event": "message", "data": json.dumps(event, default=str)}
            last_heartbeat = asyncio.get_running_loop().time()

        if run.status in ("finished", "error"):
            yield {"event": "done", "data": json.dumps({"status": run.status,
                                                        "error": run.error})}
            return

        await asyncio.sleep(0.25)
        now = asyncio.get_running_loop().time()
        if now - last_heartbeat >= 30.0:
            yield {"event": "ping", "data": "{}"}
            last_heartbeat = now


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str) -> EventSourceResponse:
    if REGISTRY.get(run_id) is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return EventSourceResponse(_event_stream(run_id))

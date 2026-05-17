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

from .registry import REGISTRY, SENTINEL


router = APIRouter()


async def _event_stream(run_id: str) -> AsyncIterator[dict[str, str]]:
    run = REGISTRY.get(run_id)
    if run is None:
        return

    # Catch up any client that subscribes after events have already been queued.
    for event in list(run.events):
        yield {"event": "message", "data": json.dumps(event, default=str)}

    # Stream live until the sentinel arrives or the run is already terminal.
    if run.status in ("finished", "error"):
        yield {"event": "done", "data": json.dumps({"status": run.status,
                                                    "error": run.error})}
        return

    while True:
        try:
            ev = await asyncio.wait_for(run.queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            # SSE heartbeat — keeps the connection alive through proxies.
            yield {"event": "ping", "data": "{}"}
            continue
        if ev is SENTINEL:
            yield {"event": "done", "data": json.dumps({"status": run.status,
                                                        "error": run.error})}
            return
        yield {"event": "message", "data": json.dumps(ev, default=str)}


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str) -> EventSourceResponse:
    if REGISTRY.get(run_id) is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return EventSourceResponse(_event_stream(run_id))

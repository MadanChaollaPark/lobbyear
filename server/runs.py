"""POST /runs starts an analyze run in the background and returns a run_id.

The run runs as an asyncio.Task inside the FastAPI event loop. The agent
loop pushes events into the Run's queue; the SSE endpoint (next commit)
drains it. Heavy VideoDB calls are sync — they'll hold the event loop for
each Claude turn, but events still flush between turns.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lobbyear.agent import run_lobby_agent_async, trace_to_dicts
from lobbyear.briefing import Briefing
from lobbyear.profile import load_profile
from lobbyear.run import (
    _connect_videodb,
    _fetch_transcript,
    _index_spoken,
    _index_video,
    _resolve_video_from_id,
    _resolve_video_from_url_or_file,
    _slugify,
    _write_viewer,
)

from .registry import REGISTRY, Run


router = APIRouter()


class StartRunRequest(BaseModel):
    client: str = Field(..., description="Path to a client profile YAML.")
    url: str | None = None
    file: str | None = None
    video_id: str | None = Field(default=None, alias="videoId")
    name: str | None = None
    language_code: str | None = None
    scene_seconds: int = 10
    scene_timeout: int = 600
    poll_interval: int = 4
    max_turns: int = 18

    class Config:
        populate_by_name = True


class StartRunResponse(BaseModel):
    run_id: str
    events_url: str
    briefing_url: str


async def _execute_run(run: Run, req: StartRunRequest) -> None:
    started = time.time()
    try:
        run.status = "running"
        run.push({"kind": "status", "name": "starting", "payload": {}})

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set on the server")

        profile = load_profile(req.client)
        run.push({"kind": "status", "name": "profile_loaded",
                  "payload": {"client": profile.name}})

        conn = _connect_videodb()
        coll = conn.get_collection()
        if req.video_id:
            video = _resolve_video_from_id(coll, req.video_id)
        else:
            video = _resolve_video_from_url_or_file(
                coll, url=req.url, file=req.file, name=req.name
            )
        video_id = str(getattr(video, "id", "unknown"))
        video_title = getattr(video, "name", None) or getattr(video, "title", None)
        video_length_s = getattr(video, "length", None)
        run.push({"kind": "status", "name": "video_ready",
                  "payload": {"id": video_id, "title": video_title,
                              "length_s": video_length_s}})

        run.push({"kind": "status", "name": "indexing_scenes", "payload": {}})
        scene_index_id, scene_records = _index_video(
            video,
            scene_seconds=req.scene_seconds,
            scene_timeout_s=req.scene_timeout,
            poll_s=req.poll_interval,
        )
        run.push({"kind": "status", "name": "scenes_indexed",
                  "payload": {"count": len(scene_records)}})

        run.push({"kind": "status", "name": "indexing_spoken", "payload": {}})
        spoken_marker = _index_spoken(video, language_code=req.language_code)
        transcript_segments = _fetch_transcript(video) if spoken_marker else []
        run.push({"kind": "status", "name": "spoken_indexed",
                  "payload": {"segments": len(transcript_segments)}})

        briefing = Briefing(
            client_name=profile.name,
            video_id=video_id,
            video_title=video_title,
            video_length_s=float(video_length_s) if video_length_s is not None else None,
            source=req.url or req.file or req.video_id or "(unknown)",
        )

        run_slug = _slugify(f"{profile.name}-{run.id}")
        repo_root = Path(__file__).resolve().parent.parent
        artifact_dir = repo_root / "artifacts" / run_slug
        artifact_dir.mkdir(parents=True, exist_ok=True)
        trace_path = artifact_dir / "trace.jsonl"
        briefing_path = artifact_dir / "briefing.json"
        trace_file = trace_path.open("w", encoding="utf-8")

        async def on_event(event: dict[str, Any]) -> None:
            run.push(event)
            trace_file.write(json.dumps(event, default=str) + "\n")
            trace_file.flush()

        try:
            result, session = await run_lobby_agent_async(
                profile=profile,
                video=video,
                scene_index_id=scene_index_id,
                spoken_index_id=spoken_marker,
                scene_records=scene_records,
                transcript_segments=transcript_segments,
                briefing=briefing,
                max_turns=req.max_turns,
                on_event=on_event,
            )
        finally:
            trace_file.close()

        briefing.finish_reason = result.finish_reason
        briefing.elapsed_s = round(time.time() - started, 2)
        payload = briefing.to_dict()
        payload["agent_trace"] = trace_to_dicts(result.trace)
        payload["search_calls"] = session.search_calls
        payload["distinct_query_count"] = len(
            {c["query"].strip().lower() for c in session.search_calls}
        )
        payload["profile"] = dataclasses.asdict(profile)
        briefing_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        _write_viewer(artifact_dir, briefing_path, trace_path)

        run.finish(payload)
        run.push({"kind": "status", "name": "finished",
                  "payload": {"mentions": len(payload["mentions"]),
                              "finish_reason": result.finish_reason}})
    except Exception as exc:  # noqa: BLE001 — surface to the SSE stream
        run.fail(str(exc))
        run.push({"kind": "status", "name": "error", "payload": {"message": str(exc)}})


@router.post("/runs", response_model=StartRunResponse)
async def start_run(req: StartRunRequest) -> StartRunResponse:
    if not (req.url or req.file or req.video_id):
        raise HTTPException(400, "Provide one of url, file, or video_id")
    run = REGISTRY.create()
    asyncio.create_task(_execute_run(run, req))
    return StartRunResponse(
        run_id=run.id,
        events_url=f"/runs/{run.id}/events",
        briefing_url=f"/runs/{run.id}",
    )


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    return {"runs": REGISTRY.list()}

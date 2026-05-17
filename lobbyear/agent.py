from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent_kit import AgentTraceEvent, ToolLoopResult, run_tool_loop

from .briefing import Briefing
from .profile import ClientProfile
from .tools import LobbySession, build_tools


SYSTEM_PROMPT_TEMPLATE = """\
You are LobbyEar, an agentic mention scanner for a public-affairs / lobbying team.

Your job: watch one indexed video and find every moment a speaker says something
that should matter to a specific paying client. You decide which angles to probe.
You are NOT a transcription pipeline — you act with judgment.

{client_block}

How to think:
1. Plan 4 to 8 distinct search angles before you start searching. Mix:
   - direct client / competitor name searches against the SPOKEN-WORD index
   - topic searches (each interest, each risk) against the SPOKEN-WORD index
   - visual evidence searches (slides, charts, name plates) against the SCENE index
   - key-actor name searches (people, institutions) against either index
2. Issue searches one at a time. After each search, look at what came back and
   decide what to investigate next. Do not just dump the seed list — adapt.
3. For each strong hit:
   a. Optionally widen with get_transcript_window for exact wording.
   b. Compile a clip on the strongest 1-2 shots.
   c. Call record_mention with an honest category, severity, and a
      why_it_matters that ties the quote to a specific client interest or risk.
4. Severity:
   - high   = direct regulatory threat, named mention of client, or competitor advantage
   - medium = topic on the watchlist discussed substantively
   - low    = passing reference or weak signal
5. Do not invent facts. If a search returns nothing for an angle, drop it and
   move on. If the whole video is irrelevant to the client, record one 'ambient'
   mention explaining the negative finding and finalize.
6. Finalize only when you have either covered the watchlist or run out of new
   signal (≥2 searches in a row with no useful hits). The runtime enforces a
   minimum of 3 distinct search queries before finalize will succeed.

Output discipline: every record_mention.why_it_matters must reference the actual
words spoken (paraphrase or short quote) — never a template phrase like
"this is relevant to the client's interests in tobacco regulation". A judge will
read these. Be specific."""


def build_system_prompt(profile: ClientProfile) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(client_block=profile.as_prompt_block())


def build_initial_user(
    *,
    video_title: str | None,
    video_length_s: float | None,
    scene_summary: str,
    spoken_summary: str,
) -> str:
    duration = (
        f"{int(video_length_s // 60)}m{int(video_length_s % 60):02d}s"
        if video_length_s
        else "unknown"
    )
    return (
        f"Begin the LobbyEar run.\n\n"
        f"Video: {video_title or '(untitled)'}\n"
        f"Duration: {duration}\n\n"
        f"Scene index excerpts:\n{scene_summary or '(none)'}\n\n"
        f"Spoken-word excerpts:\n{spoken_summary or '(none)'}\n\n"
        f"Plan your search angles, then issue your first search."
    )


def _summarize_scene_records(records: list[dict[str, Any]], limit: int = 8) -> str:
    lines: list[str] = []
    for i, rec in enumerate(records[:limit], start=1):
        start = rec.get("start") or rec.get("start_s") or 0
        end = rec.get("end") or rec.get("end_s") or 0
        desc = str(rec.get("description") or rec.get("text") or "")[:300]
        lines.append(f"{i}. {start:.1f}-{end:.1f}s: {desc}")
    return "\n".join(lines)


def _summarize_transcript(segments: list[dict[str, Any]], limit: int = 12) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments[:limit], start=1):
        start = seg.get("start") or seg.get("start_s") or 0
        text = str(seg.get("text") or "")[:240]
        lines.append(f"{i}. @{float(start):.1f}s: {text}")
    return "\n".join(lines)


async def _run(
    *,
    profile: ClientProfile,
    video: Any,
    scene_index_id: str | None,
    spoken_index_id: str | None,
    scene_records: list[dict[str, Any]],
    transcript_segments: list[dict[str, Any]],
    briefing: Briefing,
    max_turns: int,
    on_event,
) -> tuple[ToolLoopResult, LobbySession]:
    session = LobbySession(
        video=video,
        scene_index_id=scene_index_id,
        spoken_index_id=spoken_index_id,
        briefing=briefing,
    )
    if transcript_segments:
        session.transcript_cache = transcript_segments

    tools = build_tools(session)
    system_prompt = build_system_prompt(profile)
    initial_user = build_initial_user(
        video_title=briefing.video_title,
        video_length_s=briefing.video_length_s,
        scene_summary=_summarize_scene_records(scene_records),
        spoken_summary=_summarize_transcript(transcript_segments),
    )

    result = await run_tool_loop(
        system_prompt=system_prompt,
        initial_user=initial_user,
        tools=tools,
        finish_tool_names={"finalize_briefing"},
        max_turns=max_turns,
        max_tokens=2400,
        temperature=0.4,
        emit=on_event,
    )
    return result, session


def run_lobby_agent(
    *,
    profile: ClientProfile,
    video: Any,
    scene_index_id: str | None,
    spoken_index_id: str | None,
    scene_records: list[dict[str, Any]],
    transcript_segments: list[dict[str, Any]],
    briefing: Briefing,
    max_turns: int = 18,
    trace_path: Path | None = None,
) -> tuple[ToolLoopResult, LobbySession]:
    trace_file = None
    if trace_path is not None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_file = trace_path.open("w", encoding="utf-8")

    async def on_event(event: dict[str, Any]) -> None:
        kind = event.get("kind")
        name = event.get("name")
        payload = event.get("payload") or {}
        if kind == "text":
            text = str(payload.get("text", ""))[:240].replace("\n", " ")
            print(f"  [reason] {text}")
        elif kind == "tool_use":
            print(f"  [tool ] {name}({json.dumps(payload.get('input', {}))[:200]})")
        elif kind == "tool_result":
            result = payload.get("result") or {}
            is_error = payload.get("is_error")
            mark = "ERR" if is_error else "ok "
            keys = list(result.keys()) if isinstance(result, dict) else []
            print(f"  [{mark}  ] {name} -> keys={keys}")
        if trace_file is not None:
            trace_file.write(json.dumps(event, default=str) + "\n")
            trace_file.flush()

    try:
        return asyncio.run(
            _run(
                profile=profile,
                video=video,
                scene_index_id=scene_index_id,
                spoken_index_id=spoken_index_id,
                scene_records=scene_records,
                transcript_segments=transcript_segments,
                briefing=briefing,
                max_turns=max_turns,
                on_event=on_event,
            )
        )
    finally:
        if trace_file is not None:
            trace_file.close()


def trace_to_dicts(trace: list[AgentTraceEvent]) -> list[dict[str, Any]]:
    return [
        {"kind": ev.kind, "name": ev.name, "payload": ev.payload}
        for ev in trace
    ]

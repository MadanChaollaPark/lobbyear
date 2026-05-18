from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Defensive: also expose the vendored agent_kit/ when this module is imported
# directly (e.g. unit tests) before the package __init__ has run.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_kit import AgentTool  # noqa: E402

from .briefing import Briefing, Mention


def _seconds_to_timecode(seconds: float | int | None) -> str:
    total = max(0, round(float(seconds or 0)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    try:
        import json
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


@dataclass
class LobbySession:
    """Per-run state shared across tool calls."""

    video: Any
    scene_index_id: str | None
    spoken_index_id: str | None
    briefing: Briefing

    shots_by_id: dict[str, Any] = field(default_factory=dict)
    shot_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    search_calls: list[dict[str, Any]] = field(default_factory=list)
    transcript_cache: list[dict[str, Any]] | None = None
    done: bool = False

    # ---------------- search ----------------

    def _do_search(self, query: str, k: int, index_type: str) -> dict[str, Any]:
        from videodb import IndexType, SearchType  # type: ignore

        index_map = {
            "scene": (IndexType.scene, self.scene_index_id),
            "spoken": (IndexType.spoken_word, self.spoken_index_id),
        }
        if index_type not in index_map:
            return {"error": f"unknown index_type: {index_type}"}
        idx_enum, idx_id = index_map[index_type]
        if not idx_id and index_type == "scene":
            return {"error": "scene index not available — was index_scenes called?"}
        if not idx_id and index_type == "spoken":
            return {"error": "spoken-word index not available — was index_spoken_words called?"}

        safe_k = max(1, min(int(k or 5), 8))
        kwargs: dict[str, Any] = {
            "index_type": idx_enum,
            "search_type": SearchType.semantic,
            "result_threshold": safe_k,
        }
        if index_type == "scene":
            kwargs["index_id"] = idx_id
        search_id = f"s{len(self.search_calls) + 1:02d}-{index_type}"
        call = {
            "id": search_id,
            "query": query,
            "k": safe_k,
            "index": index_type,
            "shot_count": 0,
            "shots": [],
        }

        try:
            result = self.video.search(query, **kwargs)
        except Exception as exc:  # noqa: BLE001 - no-result searches still count as attempts
            call["error"] = str(exc)
            self.search_calls.append(call)
            return call

        shots = []
        for i, shot in enumerate(result.get_shots(), start=1):
            shot_id = f"{search_id}-{i}"
            self.shots_by_id[shot_id] = shot
            record = {
                "id": shot_id,
                "start_s": getattr(shot, "start", None),
                "end_s": getattr(shot, "end", None),
                "timecode": (
                    f"{_seconds_to_timecode(getattr(shot, 'start', 0))}"
                    f"-{_seconds_to_timecode(getattr(shot, 'end', 0))}"
                ),
                "text": getattr(shot, "text", None),
                "score": getattr(shot, "search_score", None),
                "stream_url": getattr(shot, "stream_url", None),
                "player_url": getattr(shot, "player_url", None),
            }
            self.shot_records[shot_id] = record
            shots.append(record)

        call["shot_count"] = len(shots)
        call["shots"] = shots
        self.search_calls.append(call)
        return call

    def search_scenes(self, query: str, k: int = 5) -> dict[str, Any]:
        return self._do_search(query, k, "scene")

    def search_spoken(self, query: str, k: int = 5) -> dict[str, Any]:
        return self._do_search(query, k, "spoken")

    # ---------------- transcript window ----------------

    def get_transcript_window(self, start_s: float, end_s: float) -> dict[str, Any]:
        if self.transcript_cache is None:
            try:
                raw = self.video.get_transcript() or []
            except Exception as exc:  # noqa: BLE001
                return {"error": f"transcript fetch failed: {exc}"}
            self.transcript_cache = [_jsonable(seg) for seg in raw]

        lo, hi = float(start_s), float(end_s)
        if hi < lo:
            lo, hi = hi, lo
        slice_ = [
            seg for seg in self.transcript_cache
            if float(seg.get("start", seg.get("start_s", 0)) or 0) <= hi
            and float(seg.get("end", seg.get("end_s", 0)) or 0) >= lo
        ]
        return {
            "window_s": [lo, hi],
            "segment_count": len(slice_),
            "segments": slice_[:25],  # cap payload
        }

    # ---------------- compile clip ----------------

    def compile_clip(self, shot_ids: list[str]) -> dict[str, Any]:
        clips = []
        for sid in shot_ids:
            shot = self.shots_by_id.get(sid)
            if shot is None:
                clips.append({"shot_id": sid, "error": "unknown shot id"})
                continue
            try:
                stream_url = shot.generate_stream()
                player_url = getattr(shot, "player_url", None)
                rec = self.shot_records.get(sid)
                if rec is not None:
                    rec["stream_url"] = stream_url
                    rec["player_url"] = player_url
                clips.append(
                    {
                        "shot_id": sid,
                        "start_s": getattr(shot, "start", None),
                        "end_s": getattr(shot, "end", None),
                        "stream_url": stream_url,
                        "player_url": player_url,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                clips.append({"shot_id": sid, "error": str(exc)})
        return {"clips": clips}

    # ---------------- record mention ----------------

    _ALLOWED_CATEGORIES = {
        "client_named",
        "competitor_named",
        "watchlist_topic",
        "key_actor",
        "regulatory_signal",
        "ambient",
    }
    _ALLOWED_SEVERITY = {"low", "medium", "high"}

    def record_mention(
        self,
        *,
        category: str,
        severity: str,
        topic: str,
        speaker_guess: str,
        start_s: float,
        end_s: float,
        transcript_quote: str,
        why_it_matters: str,
        evidence_shot_ids: list[str],
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        category = (category or "").strip().lower()
        if category not in self._ALLOWED_CATEGORIES:
            return {
                "error": f"category must be one of {sorted(self._ALLOWED_CATEGORIES)}",
            }
        sev = (severity or "").strip().lower()
        if sev not in self._ALLOWED_SEVERITY:
            return {"error": f"severity must be one of {sorted(self._ALLOWED_SEVERITY)}"}

        known = [sid for sid in (evidence_shot_ids or []) if sid in self.shot_records]
        if not known:
            return {
                "error": "evidence_shot_ids must reference shot ids returned by a prior "
                         "search_scenes / search_spoken call",
            }

        clip_urls: list[str] = []
        for sid in known:
            rec = self.shot_records[sid]
            url = rec.get("player_url") or rec.get("stream_url")
            if url:
                clip_urls.append(str(url))

        mention = Mention(
            id=f"m{len(self.briefing.mentions) + 1:02d}",
            category=category,
            severity=sev,
            topic=str(topic).strip()[:240],
            speaker_guess=str(speaker_guess).strip()[:120],
            start_s=float(start_s),
            end_s=float(end_s),
            transcript_quote=str(transcript_quote).strip()[:600],
            why_it_matters=str(why_it_matters).strip()[:600],
            evidence_shot_ids=known,
            clip_urls=clip_urls,
            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
        )
        self.briefing.mentions.append(mention)
        return {"recorded": True, "mention_id": mention.id, "mention_count": len(self.briefing.mentions)}

    # ---------------- finalize ----------------

    def finalize_briefing(
        self,
        *,
        executive_summary: str,
        recommended_actions: list[str],
        coverage_notes: str = "",
    ) -> dict[str, Any]:
        distinct_queries = {call["query"].strip().lower() for call in self.search_calls}
        if len(distinct_queries) < 3:
            return {
                "error": (
                    "Cannot finalize yet. Run at least 3 distinct search queries first "
                    "(across search_scenes and/or search_spoken)."
                ),
                "distinct_queries": len(distinct_queries),
            }
        if len(self.briefing.mentions) == 0:
            return {
                "error": (
                    "Cannot finalize with zero recorded mentions. If the video genuinely "
                    "contains nothing relevant, record at least one 'ambient' mention "
                    "explaining the negative finding."
                ),
            }
        self.briefing.executive_summary = str(executive_summary).strip()
        self.briefing.recommended_actions = [
            str(item).strip() for item in (recommended_actions or []) if str(item).strip()
        ]
        self.briefing.coverage_notes = str(coverage_notes).strip()
        self.done = True
        return {
            "finalized": True,
            "mention_count": len(self.briefing.mentions),
            "distinct_queries": len(distinct_queries),
        }


def build_tools(session: LobbySession) -> list[AgentTool]:
    return [
        AgentTool(
            name="search_scenes",
            description=(
                "Semantic search over the VideoDB scene (visual) index. Use this to find "
                "visible evidence: slides, charts, lower-thirds, agenda items, vote results, "
                "speaker name plates. Returns shots with ids, timecodes, and scene descriptions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda inp: session.search_scenes(
                query=inp["query"], k=inp.get("k", 5)
            ),
        ),
        AgentTool(
            name="search_spoken",
            description=(
                "Semantic search over the VideoDB spoken-word (transcript) index. Use this to "
                "find moments where someone SAID something relevant. Prefer this for catching "
                "verbal mentions of clients, competitors, regulations. Returns shots with "
                "timecodes and the transcribed text."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda inp: session.search_spoken(
                query=inp["query"], k=inp.get("k", 5)
            ),
        ),
        AgentTool(
            name="get_transcript_window",
            description=(
                "Return the raw transcript segments overlapping [start_s, end_s]. Use this "
                "after a search hit to capture the exact words spoken, not just the snippet."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                },
                "required": ["start_s", "end_s"],
                "additionalProperties": False,
            },
            handler=lambda inp: session.get_transcript_window(
                start_s=inp["start_s"], end_s=inp["end_s"]
            ),
        ),
        AgentTool(
            name="compile_clip",
            description=(
                "Compile one or more shots (by id, from prior search results) into a playable "
                "VideoDB clip URL. Call this on the strongest evidence before recording a mention."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "shot_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
                "required": ["shot_ids"],
                "additionalProperties": False,
            },
            handler=lambda inp: session.compile_clip(shot_ids=inp["shot_ids"]),
        ),
        AgentTool(
            name="record_mention",
            description=(
                "Append one mention to the client briefing. A 'mention' is any moment a speaker "
                "said something that should matter to the client. Categorize honestly: "
                "client_named, competitor_named, watchlist_topic, key_actor, regulatory_signal, "
                "or ambient (background signal worth noting but indirect). 'why_it_matters' must "
                "be written in your own words, grounded in the actual transcript quote — do not "
                "restate the topic. evidence_shot_ids must come from prior search results."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "client_named",
                            "competitor_named",
                            "watchlist_topic",
                            "key_actor",
                            "regulatory_signal",
                            "ambient",
                        ],
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "topic": {"type": "string"},
                    "speaker_guess": {"type": "string"},
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "transcript_quote": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "evidence_shot_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "category",
                    "severity",
                    "topic",
                    "speaker_guess",
                    "start_s",
                    "end_s",
                    "transcript_quote",
                    "why_it_matters",
                    "evidence_shot_ids",
                ],
                "additionalProperties": False,
            },
            handler=lambda inp: session.record_mention(**inp),
        ),
        AgentTool(
            name="finalize_briefing",
            description=(
                "Terminate the run. Only call this after you have run at least 3 distinct "
                "search queries and recorded the mentions you can defend. recommended_actions "
                "are 1-line, concrete next steps a lobbyist could take this week."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "executive_summary": {"type": "string"},
                    "recommended_actions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "coverage_notes": {"type": "string"},
                },
                "required": ["executive_summary", "recommended_actions"],
                "additionalProperties": False,
            },
            handler=lambda inp: session.finalize_briefing(**inp),
        ),
    ]

"""Shared fixtures and fakes for the LobbyEar test suite.

These tests must run without any external API calls. We patch:
- VideoDB SDK (`videodb.*`)
- The Anthropic-backed agent loop (`run_lobby_agent_async`)
- Any network calls in `lobbyear.source_discovery`

so the suite is deterministic and runs in seconds with no credentials.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip credentials so tests can't accidentally hit live APIs."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VIDEO_DB_API_KEY", raising=False)
    monkeypatch.delenv("VIDEODB_API_KEY", raising=False)
    monkeypatch.delenv("VIDEODB_CAPTURE_TOKEN", raising=False)


# ----- Fake VideoDB stack ---------------------------------------------------


class FakeShot:
    def __init__(self, idx: int, kind: str) -> None:
        self.start = float(idx * 10)
        self.end = float(idx * 10 + 8)
        self.text = f"{kind} shot {idx} transcript"
        self.search_score = 0.9 - 0.05 * idx
        self.stream_url = f"https://fake.videodb/stream/{kind}/{idx}"
        self.player_url = f"https://fake.videodb/player/{kind}/{idx}"

    def generate_stream(self) -> str:  # noqa: D401 - mirrors VideoDB API
        return self.stream_url


class FakeSearchResult:
    def __init__(self, shots: list[FakeShot]) -> None:
        self._shots = shots

    def get_shots(self) -> list[FakeShot]:
        return self._shots


class FakeVideo:
    def __init__(self, video_id: str = "vid_fake_001") -> None:
        self.id = video_id
        self.name = "Fake hearing — committee 2026"
        self.length = 1234.0
        self._scene_records = [
            {"start": 0.0, "end": 10.0, "description": "Speaker at podium, slide reads 'TPD3 review'"},
            {"start": 10.0, "end": 20.0, "description": "Camera on Anna Cavazzini, ENVI chair"},
        ]
        self._transcript = [
            {"start": 0.5, "end": 5.0, "text": "We are reviewing the tobacco products directive."},
            {"start": 5.0, "end": 12.0, "text": "Heated tobacco regulation must close the flavor loophole."},
            {"start": 12.0, "end": 18.0, "text": "Excise tax harmonisation is on the agenda this term."},
        ]

    def index_scenes(self, *_, **__) -> str:
        return "scene_idx_fake_001"

    def get_scene_index(self, _index_id: str) -> list[dict[str, Any]]:
        return list(self._scene_records)

    def index_spoken_words(self, *_, **__) -> None:
        return None

    def get_transcript(self) -> list[dict[str, Any]]:
        return list(self._transcript)

    def search(self, query: str, **_kwargs) -> FakeSearchResult:
        kind = "scene" if "scene" in str(_kwargs.get("index_type", "")) else "spoken"
        shots = [FakeShot(i, kind) for i in range(1, 3)]
        for shot in shots:
            shot.text = f"{query} :: {shot.text}"
        return FakeSearchResult(shots)


class FakeCollection:
    def __init__(self, video: FakeVideo) -> None:
        self._video = video

    def get_video(self, _video_id: str) -> FakeVideo:
        return self._video

    def upload(self, *, url: str | None = None, file_path: str | None = None, name: str | None = None) -> FakeVideo:
        if name:
            self._video.name = name
        return self._video


class FakeConnection:
    def __init__(self, video: FakeVideo) -> None:
        self._video = video

    def get_collection(self) -> FakeCollection:
        return FakeCollection(self._video)


@pytest.fixture
def fake_video() -> FakeVideo:
    return FakeVideo()


@pytest.fixture
def patch_videodb(monkeypatch: pytest.MonkeyPatch, fake_video: FakeVideo) -> FakeVideo:
    """Patch the videodb SDK + key validation everywhere relevant."""

    fake_module = types.ModuleType("videodb")

    class _IndexType:
        scene = "scene"
        spoken_word = "spoken_word"

    class _SearchType:
        semantic = "semantic"

    class _SceneExtractionType:
        time_based = "time_based"

    fake_module.IndexType = _IndexType
    fake_module.SearchType = _SearchType
    fake_module.SceneExtractionType = _SceneExtractionType
    fake_module.connect = lambda: FakeConnection(fake_video)

    monkeypatch.setitem(sys.modules, "videodb", fake_module)

    # Bypass the VIDEODB_API_KEY guard inside lobbyear.run._connect_videodb.
    monkeypatch.setenv("VIDEO_DB_API_KEY", "test-key")

    return fake_video


# ----- Agent loop stub ------------------------------------------------------


@pytest.fixture
def patch_agent_loop(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Any]:
    """Replace the Anthropic-backed agent loop with a deterministic stub.

    The stub records what was called and emits a small set of fake trace events
    via the on_event callback so the SSE flow has something to stream.
    """
    from agent_kit import AgentTraceEvent, ToolLoopResult
    from lobbyear import agent as agent_mod
    from lobbyear.briefing import Mention
    from server import runs as runs_mod

    calls: dict[str, Any] = {"count": 0, "last_kwargs": None}

    async def fake_loop(**kwargs):
        calls["count"] += 1
        calls["last_kwargs"] = kwargs
        session = type(
            "Session",
            (),
            {
                "search_calls": [
                    {"id": "s01-spoken", "query": "tpd3", "index": "spoken", "shots": []},
                    {"id": "s02-spoken", "query": "flavor ban", "index": "spoken", "shots": []},
                    {"id": "s03-scene", "query": "ENVI committee", "index": "scene", "shots": []},
                ]
            },
        )()
        briefing = kwargs["briefing"]
        briefing.executive_summary = "Stubbed summary."
        briefing.recommended_actions = ["follow up", "draft amendment"]
        briefing.mentions.append(
            Mention(
                id="m01",
                category="watchlist_topic",
                severity="medium",
                topic="TPD3 review",
                speaker_guess="rapporteur",
                start_s=5.0,
                end_s=12.0,
                transcript_quote="Heated tobacco regulation must close the flavor loophole.",
                why_it_matters="Direct hit on client's flavor-ban risk.",
                evidence_shot_ids=["s01-spoken-1"],
                clip_urls=["https://fake.videodb/player/spoken/1"],
                confidence=0.8,
            )
        )

        on_event = kwargs.get("on_event")
        events = [
            {"kind": "text", "name": None, "payload": {"text": "planning queries"}},
            {"kind": "tool_use", "name": "search_spoken", "payload": {"input": {"query": "tpd3"}}},
            {"kind": "tool_result", "name": "search_spoken", "payload": {"result": {"shot_count": 2}}},
            {"kind": "tool_use", "name": "finalize_briefing", "payload": {"input": {"executive_summary": "ok"}}},
            {"kind": "tool_result", "name": "finalize_briefing", "payload": {"result": {"finalized": True}}},
        ]
        trace = [
            AgentTraceEvent(kind=e["kind"], name=e["name"] or "", payload=e["payload"])
            for e in events
        ]
        if on_event:
            for ev in events:
                await on_event(ev)

        return (
            ToolLoopResult(
                finished=True,
                finish_reason="finalize_briefing",
                messages=[],
                trace=trace,
            ),
            session,
        )

    monkeypatch.setattr(agent_mod, "run_lobby_agent_async", fake_loop)
    monkeypatch.setattr(runs_mod, "run_lobby_agent_async", fake_loop)
    return calls


# ----- Source discovery network blocker -------------------------------------


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `lobbyear.source_discovery.fetch_links` to return no rows."""
    from lobbyear import source_discovery as sd

    monkeypatch.setattr(sd, "fetch_links", lambda _url: [])

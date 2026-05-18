from __future__ import annotations

import pytest

from lobbyear.briefing import Briefing
from lobbyear.tools import LobbySession, build_tools


def _session(fake_video) -> LobbySession:
    briefing = Briefing(
        client_name="ACME", video_id="vid", video_title="t",
        video_length_s=10.0, source="src"
    )
    return LobbySession(
        video=fake_video,
        scene_index_id="scene_idx",
        spoken_index_id="spoken",
        briefing=briefing,
    )


def test_build_tools_returns_expected_names(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    names = [tool.name for tool in build_tools(session)]
    assert names == [
        "search_scenes",
        "search_spoken",
        "get_transcript_window",
        "compile_clip",
        "record_mention",
        "finalize_briefing",
    ]


def test_search_scenes_appends_call_and_shot_records(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    result = session.search_scenes("podium", k=2)
    assert result["index"] == "scene"
    assert result["shot_count"] == 2
    assert session.search_calls[-1] is result
    assert all(sid in session.shots_by_id for sid in [s["id"] for s in result["shots"]])


def test_search_clamps_k_into_bounds(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    res = session.search_spoken("topic", k=99)
    assert res["k"] == 8
    # k=0 falls back to the implicit default (5), per `k or 5` in _do_search.
    res2 = session.search_spoken("topic", k=0)
    assert res2["k"] == 5
    res3 = session.search_spoken("topic", k=-3)
    assert res3["k"] == 1


def test_search_rejects_unknown_index_type(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    assert "error" in session._do_search("q", 3, "unknown")


def test_search_returns_error_when_index_missing(patch_videodb, fake_video) -> None:
    briefing = Briefing(client_name="x", video_id="v", video_title=None,
                        video_length_s=None, source="src")
    session = LobbySession(video=fake_video, scene_index_id=None,
                           spoken_index_id=None, briefing=briefing)
    assert session.search_scenes("q")["error"].startswith("scene index not available")
    assert session.search_spoken("q")["error"].startswith("spoken-word index not available")


def test_finalize_blocked_until_three_distinct_queries(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    session.search_calls = [
        {"id": "a", "query": "x", "k": 1, "index": "spoken", "shots": []},
        {"id": "b", "query": "x", "k": 1, "index": "spoken", "shots": []},
    ]
    res = session.finalize_briefing(executive_summary="s", recommended_actions=[])
    assert res["error"].startswith("Cannot finalize yet")


def test_finalize_blocked_with_zero_mentions(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    session.search_calls = [
        {"id": str(i), "query": f"q{i}", "k": 1, "index": "spoken", "shots": []}
        for i in range(3)
    ]
    res = session.finalize_briefing(executive_summary="s", recommended_actions=[])
    assert "zero recorded mentions" in res["error"]


def test_record_mention_validates_category_and_evidence(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    session.search_scenes("foo", k=2)
    valid_id = next(iter(session.shot_records))
    res = session.record_mention(
        category="not_real",
        severity="high",
        topic="t",
        speaker_guess="s",
        start_s=0,
        end_s=1,
        transcript_quote="q",
        why_it_matters="w",
        evidence_shot_ids=[valid_id],
    )
    assert "category must be" in res["error"]

    res = session.record_mention(
        category="watchlist_topic",
        severity="extreme",
        topic="t",
        speaker_guess="s",
        start_s=0,
        end_s=1,
        transcript_quote="q",
        why_it_matters="w",
        evidence_shot_ids=[valid_id],
    )
    assert "severity must be" in res["error"]

    res = session.record_mention(
        category="watchlist_topic",
        severity="high",
        topic="t",
        speaker_guess="s",
        start_s=0,
        end_s=1,
        transcript_quote="q",
        why_it_matters="w",
        evidence_shot_ids=["unknown-shot"],
    )
    assert "evidence_shot_ids" in res["error"]


def test_record_mention_happy_path(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    session.search_scenes("foo", k=2)
    sid = next(iter(session.shot_records))
    res = session.record_mention(
        category="watchlist_topic",
        severity="medium",
        topic="t",
        speaker_guess="s",
        start_s=0,
        end_s=1,
        transcript_quote="q",
        why_it_matters="w",
        evidence_shot_ids=[sid],
        confidence=0.5,
    )
    assert res["recorded"] is True
    assert session.briefing.mentions[-1].clip_urls


def test_finalize_succeeds_after_distinct_queries_and_mention(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    session.search_scenes("a")
    session.search_spoken("b")
    session.search_spoken("c")
    sid = next(iter(session.shot_records))
    session.record_mention(
        category="ambient",
        severity="low",
        topic="t",
        speaker_guess="s",
        start_s=0,
        end_s=1,
        transcript_quote="q",
        why_it_matters="w",
        evidence_shot_ids=[sid],
    )
    res = session.finalize_briefing(executive_summary="ok", recommended_actions=["go"])
    assert res["finalized"] is True
    assert session.done is True


def test_compile_clip_handles_unknown_shot(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    res = session.compile_clip(shot_ids=["does-not-exist"])
    assert res["clips"][0]["error"] == "unknown shot id"


def test_get_transcript_window_returns_overlap(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    res = session.get_transcript_window(start_s=4, end_s=11)
    assert res["segment_count"] >= 1
    assert all("text" in s for s in res["segments"])


def test_get_transcript_window_swaps_inverted_bounds(patch_videodb, fake_video) -> None:
    session = _session(fake_video)
    res = session.get_transcript_window(start_s=11, end_s=4)
    assert res["window_s"] == [4.0, 11.0]

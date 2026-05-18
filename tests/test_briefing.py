from __future__ import annotations

from lobbyear.briefing import Briefing, Mention


def test_briefing_to_dict_serialises_mentions() -> None:
    briefing = Briefing(
        client_name="X",
        video_id="v1",
        video_title="T",
        video_length_s=42.0,
        source="https://example/v",
    )
    briefing.mentions.append(
        Mention(
            id="m01",
            category="watchlist_topic",
            severity="medium",
            topic="topic",
            speaker_guess="speaker",
            start_s=1.0,
            end_s=2.0,
            transcript_quote="quote",
            why_it_matters="why",
        )
    )
    data = briefing.to_dict()
    assert data["client_name"] == "X"
    assert data["mentions"][0]["id"] == "m01"
    assert data["mentions"][0]["clip_urls"] == []

from __future__ import annotations

from lobbyear.profile import ClientProfile
from lobbyear.source_discovery import (
    build_terms,
    deduplicate,
    discover_sources,
    looks_like_policy_video,
    normalise_institution,
    score_title,
    SourceCandidate,
)


PROFILE = ClientProfile(
    name="ACME",
    sector="tobacco",
    one_liner="",
    interests=["heated tobacco"],
    risks=["flavor ban"],
    mention_triggers=["IQOS"],
    key_actors=["DG SANTE"],
    competitors=["JTI"],
)


def test_normalise_institution_canonicalises() -> None:
    assert normalise_institution("European Parliament") == "parliament"
    assert normalise_institution("Council of the EU") == "council"
    assert normalise_institution("commission") == "commission"


def test_build_terms_keeps_unique_lowercase_terms() -> None:
    terms = build_terms(PROFILE, "tobacco flavor")
    assert "tobacco" in terms
    assert "flavor" in terms
    assert "heated tobacco" in terms
    assert len(terms) == len(set(terms))


def test_score_title_counts_multi_word_match_higher() -> None:
    score, matches = score_title("Heated tobacco discussion", ["heated tobacco", "vape"])
    assert score > 0
    assert "heated tobacco" in matches


def test_looks_like_policy_video_detects_press_terms() -> None:
    assert looks_like_policy_video("Press conference by Commissioner X")
    assert not looks_like_policy_video("Random kitten compilation")


def test_deduplicate_drops_repeat_urls() -> None:
    a = SourceCandidate("a", "EC", "t", "https://x/1", 1, [], "", "url")
    b = SourceCandidate("b", "EC", "t", "https://x/1", 5, [], "", "url")
    out = deduplicate([a, b])
    assert len(out) == 1


def test_discover_sources_returns_portal_entries(block_network) -> None:
    out = discover_sources(profile=PROFILE, query="tobacco")
    assert isinstance(out, list)
    assert any(item["institution"] == "European Parliament" for item in out)
    assert any(item["institution"] == "Council of the European Union" for item in out)
    # Highest score first.
    scores = [item["score"] for item in out]
    assert scores == sorted(scores, reverse=True)


def test_discover_sources_respects_limit(block_network) -> None:
    out = discover_sources(profile=PROFILE, limit=2)
    assert len(out) == 2

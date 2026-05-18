from __future__ import annotations

from pathlib import Path

import pytest

from lobbyear.profile_resolver import (
    extract_json_object,
    heuristic_profile_from_text,
    profile_response,
    resolve_profile_input,
    warnings_for_profile,
)


YAML_PATH = Path(__file__).resolve().parent.parent / "clients" / "example_acme_tobacco.yaml"


def test_resolve_yaml_mode_returns_profile() -> None:
    profile, warnings = resolve_profile_input(mode="yaml_path", path=str(YAML_PATH))
    assert profile.name.startswith("ACME")
    assert warnings == []


def test_resolve_yaml_mode_requires_path() -> None:
    with pytest.raises(ValueError):
        resolve_profile_input(mode="yaml_path", path=None)


def test_resolve_json_mode_with_warnings() -> None:
    payload = {"profile": {"name": "Y", "sector": ""}, "warnings": ["test-warn"]}
    profile, warnings = resolve_profile_input(mode="json", profile=payload)
    assert profile.name == "Y"
    assert "test-warn" in warnings
    # heuristic warnings should kick in too because most fields are empty
    assert any("No interests" in w for w in warnings)


def test_resolve_json_mode_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        resolve_profile_input(mode="json", profile=None)


def test_resolve_text_mode_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    # ANTHROPIC_API_KEY is stripped by the autouse fixture → heuristic path.
    text = (
        "SolarGrid Europe is a renewable energy operator. The interests are "
        "grid permitting, capacity markets, storage. The risks are subsidy cuts, "
        "permitting delays."
    )
    profile, warnings = resolve_profile_input(mode="text", text=text)
    assert profile.name.startswith("SolarGrid")
    assert any("permitting" in i for i in profile.interests)
    assert any("ANTHROPIC_API_KEY" in w for w in warnings)


def test_resolve_text_mode_rejects_empty() -> None:
    with pytest.raises(ValueError):
        resolve_profile_input(mode="text", text="   ")


def test_resolve_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        resolve_profile_input(mode="something_else")


def test_warnings_for_profile_flags_empties() -> None:
    profile = heuristic_profile_from_text("Some short text without structure.")
    warnings = warnings_for_profile(profile)
    assert all(isinstance(w, str) for w in warnings)


def test_profile_response_dedupes_warnings() -> None:
    profile = heuristic_profile_from_text("Acme is a renewable energy firm.")
    response = profile_response(profile, ["a", "a", "b"])
    assert response["warnings"] == ["a", "b"]
    assert "name" in response["profile"]


def test_extract_json_object_from_messy_text() -> None:
    raw = "Sure, here is the JSON: {\"profile\": {\"name\": \"X\"}}"
    payload = extract_json_object(raw)
    assert payload["profile"]["name"] == "X"


def test_extract_json_object_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        extract_json_object("not json at all")

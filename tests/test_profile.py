from __future__ import annotations

from pathlib import Path

import pytest

from lobbyear.profile import (
    ClientProfile,
    load_profile,
    profile_from_mapping,
    profile_to_dict,
)


CLIENT_YAML = Path(__file__).resolve().parent.parent / "clients" / "example_acme_tobacco.yaml"


def test_example_profile_yaml_loads() -> None:
    profile = load_profile(CLIENT_YAML)
    assert isinstance(profile, ClientProfile)
    assert profile.name == "ACME Tobacco Europe"
    assert "heated tobacco" in " ".join(profile.interests).lower()
    assert profile.sensitivity in {"low", "medium", "high"}


def test_profile_from_mapping_defaults() -> None:
    profile = profile_from_mapping({"name": "X", "sector": "S", "one_liner": "ol"})
    assert profile.interests == []
    assert profile.risks == []
    assert profile.competitors == []
    assert profile.sensitivity == "medium"


def test_profile_from_mapping_strips_blanks() -> None:
    profile = profile_from_mapping(
        {"name": " Test ", "interests": ["  a  ", "", "  b"]}
    )
    assert profile.name == "Test"
    assert profile.interests == ["a", "b"]


def test_profile_from_mapping_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        profile_from_mapping({"name": "X", "interests": "not a list"})


def test_profile_to_dict_roundtrips() -> None:
    profile = load_profile(CLIENT_YAML)
    data = profile_to_dict(profile)
    rebuilt = profile_from_mapping(data)
    assert rebuilt == profile


def test_prompt_block_has_every_section() -> None:
    profile = load_profile(CLIENT_YAML)
    block = profile.as_prompt_block()
    for header in ("Client:", "Sector:", "Interests:", "Risks:",
                   "Mention triggers", "Key actors:", "Competitors:"):
        assert header in block


def test_load_profile_yaml_mapping_required(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_profile(bad)

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClientProfile:
    name: str
    sector: str
    one_liner: str
    interests: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    mention_triggers: list[str] = field(default_factory=list)
    key_actors: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    sensitivity: str = "medium"

    def as_prompt_block(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"  - {item}" for item in items) if items else "  (none)"

        return (
            f"Client: {self.name}\n"
            f"Sector: {self.sector}\n"
            f"One-liner: {self.one_liner.strip()}\n"
            f"Sensitivity: {self.sensitivity}\n"
            f"Interests:\n{bullets(self.interests)}\n"
            f"Risks:\n{bullets(self.risks)}\n"
            f"Mention triggers (literal phrases to listen for):\n{bullets(self.mention_triggers)}\n"
            f"Key actors:\n{bullets(self.key_actors)}\n"
            f"Competitors:\n{bullets(self.competitors)}"
        )


def load_profile(path: str | Path) -> ClientProfile:
    import yaml

    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Profile must be a YAML mapping, got {type(data).__name__}")
    return profile_from_mapping(data)


def profile_from_mapping(data: dict[str, Any]) -> ClientProfile:
    def as_str_list(value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("expected a list")
        return [str(item).strip() for item in value if str(item).strip()]

    return ClientProfile(
        name=str(data.get("name", "")).strip() or "Unnamed Client",
        sector=str(data.get("sector", "")).strip(),
        one_liner=str(data.get("one_liner", "")).strip(),
        interests=as_str_list(data.get("interests")),
        risks=as_str_list(data.get("risks")),
        mention_triggers=as_str_list(data.get("mention_triggers")),
        key_actors=as_str_list(data.get("key_actors")),
        competitors=as_str_list(data.get("competitors")),
        sensitivity=str(data.get("sensitivity", "medium")).strip().lower() or "medium",
    )


def profile_to_dict(profile: ClientProfile) -> dict[str, Any]:
    return dataclasses.asdict(profile)

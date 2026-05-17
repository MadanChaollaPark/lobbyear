from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Mention:
    id: str
    category: str            # "client_named", "competitor_named", "watchlist_topic",
                             # "key_actor", "regulatory_signal", "ambient"
    severity: str            # "low", "medium", "high"
    topic: str
    speaker_guess: str
    start_s: float
    end_s: float
    transcript_quote: str
    why_it_matters: str
    evidence_shot_ids: list[str] = field(default_factory=list)
    clip_urls: list[str] = field(default_factory=list)
    confidence: float = 0.0  # 0..1, model-reported


@dataclass
class Briefing:
    client_name: str
    video_id: str
    video_title: str | None
    video_length_s: float | None
    source: str               # url, file path, or capture session id
    mentions: list[Mention] = field(default_factory=list)
    executive_summary: str = ""
    recommended_actions: list[str] = field(default_factory=list)
    coverage_notes: str = ""
    finish_reason: str = ""
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

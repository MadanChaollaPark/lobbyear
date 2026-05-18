from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Iterable

from .profile import ClientProfile


@dataclass(frozen=True)
class SourceCandidate:
    id: str
    institution: str
    title: str
    url: str
    score: int
    matched_terms: list[str]
    reason: str
    ingest_hint: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        self._href = urllib.parse.urljoin(self.base_url, href)
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        text = re.sub(r"\s+", " ", " ".join(self._parts)).strip()
        if text:
            self.links.append((self._href, html.unescape(text)))
        self._href = None
        self._parts = []


def discover_sources(
    *,
    profile: ClientProfile,
    query: str = "",
    institutions: Iterable[str] | None = None,
    limit: int = 12,
) -> list[dict[str, object]]:
    selected = {normalise_institution(item) for item in (institutions or []) if item}
    if not selected:
        selected = {"parliament", "commission", "council"}
    terms = build_terms(profile, query)

    candidates: list[SourceCandidate] = []
    if "commission" in selected:
        candidates.extend(discover_commission(terms))
    if "parliament" in selected:
        candidates.extend(discover_parliament(terms))
    if "council" in selected:
        candidates.extend(discover_council(terms))

    ranked = sorted(deduplicate(candidates), key=lambda item: item.score, reverse=True)
    return [item.to_dict() for item in ranked[: max(1, min(limit, 30))]]


def normalise_institution(value: str) -> str:
    value = value.strip().lower()
    if value in {"ep", "parliament", "european parliament"}:
        return "parliament"
    if value in {"ec", "commission", "european commission"}:
        return "commission"
    if value in {"council", "european council", "council of the eu"}:
        return "council"
    return value


def discover_commission(terms: list[str]) -> list[SourceCandidate]:
    base = "https://audiovisual.ec.europa.eu/en/media/video"
    links = fetch_links(base)
    candidates: list[SourceCandidate] = []
    for href, title in links:
        if "/en/media/video/" not in href:
            continue
        if href.rstrip("/") == base:
            continue
        score, matches = score_title(title, terms)
        if score == 0 and not looks_like_policy_video(title):
            continue
        candidates.append(
            SourceCandidate(
                id=f"commission-{stable_id(href)}",
                institution="European Commission",
                title=clean_title(title),
                url=href,
                score=score + 8,
                matched_terms=matches,
                reason="Matched against recent Commission AV Portal video material.",
                ingest_hint="url",
            )
        )

    candidates.append(
        SourceCandidate(
            id="commission-ebs-schedule",
            institution="European Commission",
            title="EBS schedule and recent Commission live/on-demand events",
            url="https://audiovisual.ec.europa.eu/en/ebs/1/",
            score=portal_score("commission", terms),
            matched_terms=top_terms(terms),
            reason="Use when the agent should inspect current Commission press events and EBS items.",
            ingest_hint="capture_or_url",
        )
    )
    return candidates


def discover_parliament(terms: list[str]) -> list[SourceCandidate]:
    candidates = [
        SourceCandidate(
            id="parliament-committee-webstreaming",
            institution="European Parliament",
            title="European Parliament committee webstreaming recordings",
            url="https://www.europarl.europa.eu/committees/en/meetings/webstreaming",
            score=portal_score("parliament", terms),
            matched_terms=top_terms(terms),
            reason="Use for committee meetings such as ENVI, ITRE, ECON, IMCO, LIBE, and TAX.",
            ingest_hint="capture_or_url",
        ),
        SourceCandidate(
            id="parliament-multimedia-webstreaming",
            institution="European Parliament",
            title="European Parliament Multimedia Centre streaming agenda",
            url="https://multimedia.europarl.europa.eu/en/webstreaming",
            score=portal_score("parliament", terms) - 2,
            matched_terms=top_terms(terms),
            reason="Official EP live and on-demand webstreaming entry point.",
            ingest_hint="capture_or_url",
        ),
    ]
    best_term = next((term for term in terms if len(term) > 4), "")
    if best_term:
        search_url = "https://multimedia.europarl.europa.eu/en/search?" + urllib.parse.urlencode(
            {"text": best_term, "mediaType": "video"}
        )
        candidates.append(
            SourceCandidate(
                id=f"parliament-search-{stable_id(best_term)}",
                institution="European Parliament",
                title=f"EP Multimedia video search for {best_term}",
                url=search_url,
                score=portal_score("parliament", terms) + 3,
                matched_terms=[best_term],
                reason="Searches the official EP Multimedia Centre for matching video material.",
                ingest_hint="capture_or_url",
            )
        )
    return candidates


def discover_council(terms: list[str]) -> list[SourceCandidate]:
    return [
        SourceCandidate(
            id="council-live",
            institution="Council of the European Union",
            title="Council Live scheduled and archived webcasts",
            url="https://video.consilium.europa.eu/home/en",
            score=portal_score("council", terms),
            matched_terms=top_terms(terms),
            reason="Use for Council public sessions, press conferences, arrivals, and round tables.",
            ingest_hint="capture_or_url",
        ),
        SourceCandidate(
            id="council-press",
            institution="Council of the European Union",
            title="Council press and media events",
            url="https://www.consilium.europa.eu/en/press/",
            score=portal_score("council", terms) - 3,
            matched_terms=top_terms(terms),
            reason="Use to locate Council media advisories and official event pages before capture.",
            ingest_hint="capture_or_url",
        ),
    ]


def fetch_links(url: str) -> list[tuple[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "user-agent": "LobbyEar source discovery/0.1 (+local hackathon demo)",
            "accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            content = response.read(1_500_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    parser = _LinkExtractor(url)
    parser.feed(content.decode(charset, errors="replace"))
    return parser.links


def build_terms(profile: ClientProfile, query: str) -> list[str]:
    raw: list[str] = [query]
    raw.extend(profile.interests)
    raw.extend(profile.risks)
    raw.extend(profile.mention_triggers)
    raw.extend(profile.key_actors)
    raw.extend(profile.competitors)
    raw.append(profile.sector)

    terms: list[str] = []
    for item in raw:
        cleaned = re.sub(r"\s+", " ", item.strip().lower())
        if len(cleaned) >= 4:
            terms.append(cleaned)
        for token in re.split(r"[^a-z0-9]+", cleaned):
            if len(token) >= 5:
                terms.append(token)
    return list(dict.fromkeys(terms))[:80]


def score_title(title: str, terms: list[str]) -> tuple[int, list[str]]:
    title_l = title.lower()
    score = 0
    matches: list[str] = []
    for term in terms:
        if term in title_l:
            score += 12 if " " in term else 5
            matches.append(term)
    if looks_like_policy_video(title):
        score += 6
    return score, matches[:8]


def looks_like_policy_video(title: str) -> bool:
    title_l = title.lower()
    signals = [
        "press conference",
        "press point",
        "commissioner",
        "committee",
        "council",
        "parliament",
        "read-out",
        "meeting",
        "speech",
        "sounbite",
        "soundbite",
    ]
    return any(signal in title_l for signal in signals)


def portal_score(institution: str, terms: list[str]) -> int:
    score = 12
    joined = " ".join(terms)
    if institution == "parliament" and any(item in joined for item in ["envi", "itre", "committee", "parliament"]):
        score += 12
    if institution == "commission" and any(item in joined for item in ["commission", "dg ", "commissioner", "sante", "taxud"]):
        score += 12
    if institution == "council" and any(item in joined for item in ["council", "minister", "excise", "tax", "health"]):
        score += 12
    return score


def top_terms(terms: list[str], limit: int = 5) -> list[str]:
    return terms[:limit]


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return title[:220]


def stable_id(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[-80:] or "source"


def deduplicate(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    unique: list[SourceCandidate] = []
    for candidate in candidates:
        key = candidate.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique

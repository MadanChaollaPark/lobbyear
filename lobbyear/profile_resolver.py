from __future__ import annotations

import json
import os
import re
from typing import Any

from .profile import ClientProfile, load_profile, profile_from_mapping, profile_to_dict


PROFILE_SCHEMA = {
    "name": "SolarGrid Europe",
    "sector": "Renewable energy and grid storage",
    "one_liner": "One sentence description of the client.",
    "interests": ["grid permitting", "state aid", "capacity markets"],
    "risks": ["permitting delays", "subsidy cuts"],
    "mention_triggers": ["SolarGrid", "grid flexibility", "storage"],
    "key_actors": ["DG ENER", "ITRE committee"],
    "competitors": [],
    "sensitivity": "medium",
}


def resolve_profile_input(
    *,
    mode: str,
    path: str | None = None,
    profile: dict[str, Any] | None = None,
    text: str | None = None,
) -> tuple[ClientProfile, list[str]]:
    mode = (mode or "yaml_path").strip().lower()
    if mode == "yaml_path":
        if not path:
            raise ValueError("profile path is required")
        return load_profile(path), []
    if mode == "json":
        if not isinstance(profile, dict):
            raise ValueError("profile JSON object is required")
        profile_data = profile.get("profile") if isinstance(profile.get("profile"), dict) else profile
        provided_warnings = profile.get("warnings") if isinstance(profile.get("warnings"), list) else []
        resolved = profile_from_mapping(profile_data)
        return resolved, [str(item) for item in provided_warnings] + warnings_for_profile(resolved)
    if mode == "text":
        if not text or not text.strip():
            raise ValueError("profile text is required")
        return profile_from_text(text)
    raise ValueError(f"unknown profile mode: {mode}")


def profile_from_text(text: str) -> tuple[ClientProfile, list[str]]:
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return profile_from_text_with_claude(text)
        except Exception as exc:  # noqa: BLE001
            profile = heuristic_profile_from_text(text)
            warnings = warnings_for_profile(profile)
            warnings.append(f"Claude extraction failed; used heuristic fallback ({type(exc).__name__}).")
            return profile, warnings

    profile = heuristic_profile_from_text(text)
    warnings = warnings_for_profile(profile)
    warnings.append("ANTHROPIC_API_KEY is not set; used heuristic text extraction.")
    return profile, warnings


def profile_from_text_with_claude(text: str) -> tuple[ClientProfile, list[str]]:
    from anthropic import Anthropic  # type: ignore

    client = Anthropic()
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    response = client.messages.create(
        model=model,
        max_tokens=1200,
        temperature=0.1,
        system=(
            "Extract a lobbying/public-affairs monitoring client profile. "
            "Return only valid JSON with keys profile and warnings. "
            "profile must match this schema exactly: "
            f"{json.dumps(PROFILE_SCHEMA, ensure_ascii=True)}"
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "Convert this client brief into the profile JSON. "
                    "Use empty arrays when a field is not present. "
                    "Use sensitivity low, medium, or high.\n\n"
                    f"{text[:8000]}"
                ),
            }
        ],
    )
    content = "\n".join(
        str(getattr(block, "text", ""))
        for block in response.content
        if getattr(block, "type", None) == "text"
    )
    payload = extract_json_object(content)
    profile_data = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    profile = profile_from_mapping(profile_data)
    return profile, [str(item) for item in warnings] + warnings_for_profile(profile)


def heuristic_profile_from_text(text: str) -> ClientProfile:
    clean = re.sub(r"\s+", " ", text).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", clean)[0] if clean else ""
    name = infer_name(clean)
    terms = infer_terms(clean)
    risks = [term for term in terms if is_risk_term(term)]
    interests = [term for term in terms if term not in risks]
    return ClientProfile(
        name=name,
        sector=infer_sector(clean),
        one_liner=first_sentence[:220],
        interests=interests[:10],
        risks=risks[:10],
        mention_triggers=infer_triggers(clean, name),
        key_actors=infer_key_actors(clean),
        competitors=[],
        sensitivity="medium",
    )


def infer_name(text: str) -> str:
    match = re.search(r"(?:client|company|organisation|organization)\s*[:=-]\s*([A-Z][^.;,\n]{2,80})", text)
    if match:
        return match.group(1).strip()
    match = re.match(r"([A-Z][A-Za-z0-9& .'-]{2,80})\s+(?:is|are|operates|works)", text)
    if match:
        return match.group(1).strip()
    return "Unnamed Client"


def infer_sector(text: str) -> str:
    lowered = text.lower()
    sectors = [
        "renewable energy",
        "grid storage",
        "tobacco",
        "nicotine",
        "pharmaceutical",
        "transport",
        "technology",
        "financial services",
        "agriculture",
        "chemicals",
        "defence",
        "healthcare",
    ]
    matches = [sector for sector in sectors if sector in lowered]
    return ", ".join(matches[:3]) if matches else "target sector"


def infer_terms(text: str) -> list[str]:
    lowered = text.lower()
    patterns = [
        r"(?:care about|interests? are|watching|monitoring|concerned about)\s+([^.;]+)",
        r"(?:risks? are|worried about|threats? are)\s+([^.;]+)",
    ]
    terms: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, lowered):
            terms.extend(split_terms(match.group(1)))
    if not terms:
        terms.extend(split_terms(lowered))
    return list(dict.fromkeys(term for term in terms if 3 <= len(term) <= 80))[:20]


def split_terms(text: str) -> list[str]:
    chunks = re.split(r",|;|\band\b|\bor\b", text)
    return [re.sub(r"[^a-z0-9 /&-]+", "", chunk).strip(" -") for chunk in chunks if chunk.strip()]


def is_risk_term(term: str) -> bool:
    return any(
        re.search(rf"\b{re.escape(signal)}s?\b", term)
        for signal in [
            "ban",
            "cut",
            "delay",
            "risk",
            "obligation",
            "fine",
            "tax",
            "cap",
            "restriction",
            "compliance",
        ]
    )


def infer_triggers(text: str, name: str) -> list[str]:
    triggers = [name] if name != "Unnamed Client" else []
    quoted = re.findall(r'"([^"]{3,80})"|\'([^\']{3,80})\'', text)
    for left, right in quoted:
        triggers.append(left or right)
    caps = re.findall(r"\b[A-Z][A-Z0-9&-]{2,}(?:\s+[A-Z][A-Z0-9&-]{2,})?\b", text)
    triggers.extend(caps)
    return list(dict.fromkeys(item.strip() for item in triggers if item.strip()))[:10]


def infer_key_actors(text: str) -> list[str]:
    actor_patterns = [
        r"\bDG\s+[A-Z]{2,}\b",
        r"\b[A-Z]{3,}\s+committee\b",
        r"\b(?:ENVI|ITRE|ECON|IMCO|LIBE|TAXUD|SANTE|ENER)\b",
    ]
    actors: list[str] = []
    for pattern in actor_patterns:
        actors.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return list(dict.fromkeys(actor.strip() for actor in actors if actor.strip()))[:10]


def warnings_for_profile(profile: ClientProfile) -> list[str]:
    warnings: list[str] = []
    if not profile.sector:
        warnings.append("No sector provided.")
    if not profile.interests:
        warnings.append("No interests provided.")
    if not profile.risks:
        warnings.append("No risks provided.")
    if not profile.mention_triggers:
        warnings.append("No mention triggers provided.")
    if not profile.competitors:
        warnings.append("No competitors provided.")
    return warnings


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("Claude did not return a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("profile extraction did not return a JSON object")
    return parsed


def profile_response(profile: ClientProfile, warnings: list[str]) -> dict[str, Any]:
    return {"profile": profile_to_dict(profile), "warnings": list(dict.fromkeys(warnings))}

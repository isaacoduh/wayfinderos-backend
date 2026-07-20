from __future__ import annotations

import re

from app.services.utils import clean_extracted_text


def extract_trip_context_updates(text: str) -> dict:
    updates: dict = {}

    origin_match = re.search(
        r"\b(?:coming|travelling|traveling|leaving|starting)\s+from\s+([A-Z][A-Za-z .'-]{1,60})",
        text,
    )
    if origin_match:
        origin = clean_extracted_text(origin_match.group(1))
        origin = re.split(r"\s+(?:for|on|with|and|but)\s+", origin, maxsplit=1)[0]
        if origin:
            updates["origin"] = origin

    budget_match = re.search(
        r"\b(?:my\s+)?budget(?:\s+is|\s+of|:)?\s*([£$€]\s?\d[\d,]*(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if budget_match:
        updates["budget"] = clean_extracted_text(budget_match.group(1))

    duration_match = re.search(r"\b(?:for|going for|we are going for)\s+(\d+)\s+days?\b", text, re.IGNORECASE)
    if duration_match:
        updates["duration"] = f"{duration_match.group(1)} days"

    avoid_match = re.search(r"\bavoid\s+([^.!?]+)", text, re.IGNORECASE)
    if avoid_match:
        constraint = clean_extracted_text(f"avoid {avoid_match.group(1)}")
        if constraint:
            updates.setdefault("constraints", []).append(constraint)

    preference_match = re.search(r"\b(?:want|prefer|interested in|like)\s+([^.!?]+)", text, re.IGNORECASE)
    if preference_match:
        preference = clean_extracted_text(preference_match.group(1))
        if preference and len(preference) <= 80:
            updates.setdefault("preferences", []).append(preference)

    return updates


def append_unique(values: list[str], new_values: list[str]) -> list[str]:
    normalized = {value.lower(): value for value in values}
    for value in new_values:
        key = value.lower()
        if key not in normalized:
            normalized[key] = value
    return list(normalized.values())


def merge_planning_context(existing: dict | None, updates: dict) -> dict:
    context = dict(existing or {})

    for key in ("origin", "budget", "duration"):
        if updates.get(key):
            context[key] = updates[key]

    if updates.get("duration"):
        context["known_details"] = append_unique(context.get("known_details", []), [updates["duration"]])

    for key in ("constraints", "preferences"):
        if updates.get(key):
            context[key] = append_unique(context.get(key, []), updates[key])

    return context

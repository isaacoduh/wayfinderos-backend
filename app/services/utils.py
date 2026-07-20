from __future__ import annotations

import json
import re
from datetime import datetime


def ndjson(event: dict) -> str:
    return json.dumps(event) + "\n"


def get_event_error_message(event) -> str:
    error = getattr(event, "error", None)
    if error and getattr(error, "message", None):
        return error.message

    message = getattr(event, "message", None)
    if message:
        return message

    return "Wayfinder could not complete this request. Please try again."


def format_date_value(value) -> str:
    return value.isoformat() if value else "not set"


def format_time_value(value) -> str:
    return value.strftime("%H:%M") if value else "TBD"


def clean_extracted_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" .,!?:;")).strip()


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def get_response_text(response) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks)


def parse_time_string(value: str | None):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def normalize_place_key(name: str, city: str | None, country: str | None) -> tuple[str, str, str]:
    return (name.strip().lower(), (city or "").strip().lower(), (country or "").strip().lower())

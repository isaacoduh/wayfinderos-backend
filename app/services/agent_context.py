from __future__ import annotations

import json

from app.config import CHAT_ROLES
from app.models import ChatMessage, ItineraryDay, Trip, TripPlace
from app.services.utils import format_date_value, format_time_value


def summarize_itinerary(days: list[ItineraryDay]) -> list[str]:
    if not days:
        return ["No itinerary days saved yet."]

    lines = []
    for day in days[:8]:
        day_title = day.title or f"Day {day.day_number}"
        item_bits = []
        for item in day.items[:8]:
            flags = []
            if item.is_locked:
                flags.append("locked")
            if item.is_booked:
                flags.append("booked")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            item_bits.append(f"{format_time_value(item.start_time)} {item.title}{flag_text}")
        items_text = "; ".join(item_bits) if item_bits else "No saved items"
        lines.append(f"Day {day.day_number}: {day_title} - {items_text}")

    return lines


def summarize_places(places: list[TripPlace]) -> list[str]:
    if not places:
        return ["No saved places yet."]

    grouped: dict[str, list[str]] = {}
    for trip_place in places:
        place = trip_place.place
        detail = place.name
        meta = ", ".join(part for part in (place.category, place.city, place.country) if part)
        if meta:
            detail = f"{detail} ({meta})"
        if trip_place.notes:
            detail = f"{detail}: {trip_place.notes}"
        grouped.setdefault(trip_place.status, []).append(detail)

    lines = []
    for status in sorted(grouped):
        lines.append(f"{status}: {'; '.join(grouped[status][:10])}")
    return lines


def summarize_locked_booked(days: list[ItineraryDay]) -> list[str]:
    items = []
    for day in days:
        for item in day.items:
            if item.is_locked or item.is_booked:
                flags = []
                if item.is_locked:
                    flags.append("locked")
                if item.is_booked:
                    flags.append("booked")
                items.append(f"Day {day.day_number}: {item.title} ({', '.join(flags)})")
    return items or ["No locked or booked itinerary items yet."]


def summarize_day_items(day: ItineraryDay, *, protected: bool | None = None) -> list[str]:
    lines = []
    for item in day.items:
        is_protected = item.is_locked or item.is_booked
        if protected is not None and is_protected != protected:
            continue

        flags = []
        if item.is_locked:
            flags.append("locked")
        if item.is_booked:
            flags.append("booked")
        flag_text = f"; {'/'.join(flags)}" if flags else ""
        place_text = f"; place={item.place.name}" if item.place else ""
        lines.append(
            (
                f"- id={item.id}; sort={item.sort_order}; {format_time_value(item.start_time)}-"
                f"{format_time_value(item.end_time)}; title={item.title}; category={item.category or 'Plan'}"
                f"{place_text}{flag_text}; notes={item.description or 'none'}"
            )
        )

    return lines or ["- None"]


def build_trip_context_prompt(
    trip: Trip,
    recent_messages: list[ChatMessage],
    days: list[ItineraryDay],
    places: list[TripPlace],
) -> str:
    planning_context = trip.planning_context or {}
    recent_history = [
        f"{message.role}: {message.content[:700]}"
        for message in recent_messages[-12:]
        if message.role in CHAT_ROLES
    ]
    missing_details = []
    if not planning_context.get("origin"):
        missing_details.append("origin not yet known")
    if not trip.start_date and not trip.end_date and not planning_context.get("duration"):
        missing_details.append("dates or duration not yet known")
    if not trip.budget_amount and not planning_context.get("budget"):
        missing_details.append("budget not yet known")

    return "\n".join(
        [
            "Trip workspace context:",
            f"- Title: {trip.title}",
            f"- Destination: {trip.destination}",
            f"- Start date: {format_date_value(trip.start_date)}",
            f"- End date: {format_date_value(trip.end_date)}",
            f"- Status/progress: {trip.status}, {trip.progress}% ready",
            f"- Budget: {trip.budget_amount if trip.budget_amount is not None else planning_context.get('budget', 'not set')}",
            f"- Planning context JSON: {json.dumps(planning_context, default=str)}",
            "",
            "Recent chat history:",
            *(recent_history or ["No prior chat messages."]),
            "",
            "Current itinerary summary:",
            *summarize_itinerary(days),
            "",
            "Current trip places grouped by status:",
            *summarize_places(places),
            "",
            "Locked/booked itinerary items:",
            *summarize_locked_booked(days),
            "",
            "Known assumptions or missing details:",
            *(missing_details or ["No obvious missing basics."]),
        ]
    )


def build_regenerate_day_prompt(
    *,
    trip: Trip,
    recent_messages: list[ChatMessage],
    days: list[ItineraryDay],
    places: list[TripPlace],
    day: ItineraryDay,
    instruction: str,
) -> str:
    protected_count = len([item for item in day.items if item.is_locked or item.is_booked])
    editable_count = len([item for item in day.items if not item.is_locked and not item.is_booked])

    return "\n".join(
        [
            build_trip_context_prompt(trip, recent_messages, days, places),
            "",
            "Selected day for editable regeneration:",
            f"- Day id: {day.id}",
            f"- Day number: {day.day_number}",
            f"- Date: {format_date_value(day.date)}",
            f"- Current title: {day.title or f'Day {day.day_number}'}",
            f"- Current summary: {day.summary or 'No summary saved.'}",
            f"- Protected item count: {protected_count}",
            f"- Editable item count: {editable_count}",
            "",
            "Protected items. These are immutable and must not appear in your returned items:",
            *summarize_day_items(day, protected=True),
            "",
            "Editable items. These may be replaced:",
            *summarize_day_items(day, protected=False),
            "",
            "User instruction:",
            instruction,
            "",
            "Generate only the replacement unlocked/unbooked items for this selected day.",
            "Sequence them realistically around the protected items without changing protected item details.",
        ]
    )


def build_structured_trip_prompt(
    trip: Trip,
    recent_messages: list[ChatMessage],
    days: list[ItineraryDay],
    places: list[TripPlace],
) -> str:
    return "\n".join(
        [
            build_trip_context_prompt(trip, recent_messages, days, places),
            "",
            "Build a complete trip plan now.",
            "Persistable artifact requirements:",
            "- Generate itinerary days/items for the trip duration if known.",
            "- Include transport guidance from the known origin when present.",
            "- Recommend concrete places in the destination.",
            "- Include budget notes and category estimates.",
            "- Include booking/checklist tasks.",
            "- Keep locked/booked saved itinerary items respected as immovable constraints.",
        ]
    )

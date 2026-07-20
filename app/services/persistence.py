from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.schemas import BuildTripItineraryDay, BuildTripItineraryItem, BuildTripOutput, BuildTripPlace, RegenerateDayOutput
from app.models import ChecklistItem, ItineraryDay, ItineraryItem, Place, Trip, TripPlace, utc_now
from app.services.utils import normalize_place_key, parse_time_string


def get_or_create_generated_place(db: Session, place_data: BuildTripPlace) -> Place:
    name = place_data.name.strip()
    city = place_data.city.strip() if place_data.city else None
    country = place_data.country.strip() if place_data.country else None

    place = db.scalar(
        select(Place).where(
            func.lower(Place.name) == name.lower(),
            func.coalesce(func.lower(Place.city), "") == (city or "").lower(),
            func.coalesce(func.lower(Place.country), "") == (country or "").lower(),
        )
    )
    if place:
        if place_data.category and not place.category:
            place.category = place_data.category
        return place

    place = Place(
        name=name,
        category=place_data.category,
        city=city,
        country=country,
    )
    db.add(place)
    db.flush()
    return place


def link_place_to_trip(
    db: Session,
    *,
    trip_id: str,
    place: Place,
    notes: str | None = None,
    priority: int | None = None,
) -> TripPlace:
    trip_place = db.scalar(select(TripPlace).where(TripPlace.trip_id == trip_id, TripPlace.place_id == place.id))
    if trip_place:
        if notes and not trip_place.notes:
            trip_place.notes = notes
        if priority is not None and trip_place.priority is None:
            trip_place.priority = priority
        return trip_place

    trip_place = TripPlace(
        trip_id=trip_id,
        place_id=place.id,
        status="suggested",
        notes=notes,
        priority=priority,
    )
    db.add(trip_place)
    db.flush()
    return trip_place


def protected_item_signature(item: ItineraryItem) -> tuple[str, str | None]:
    return (item.title.strip().lower(), item.start_time.strftime("%H:%M") if item.start_time else None)


def filter_protected_duplicates(
    *,
    generated_items: list[BuildTripItineraryItem],
    protected_items: list[ItineraryItem],
) -> tuple[list[BuildTripItineraryItem], int]:
    protected_signatures = {protected_item_signature(item) for item in protected_items}
    filtered = []
    skipped = 0

    for item in generated_items:
        signature = (item.title.strip().lower(), item.start_time)
        title_matches_protected = any(item.title.strip().lower() == protected.title.strip().lower() for protected in protected_items)
        if signature in protected_signatures or title_matches_protected:
            skipped += 1
            continue
        filtered.append(item)

    return filtered, skipped


def persist_regenerate_day_output(
    db: Session,
    *,
    trip: Trip,
    day: ItineraryDay,
    output: RegenerateDayOutput,
) -> dict:
    places_by_key: dict[tuple[str, str, str], Place] = {}
    for index, place_data in enumerate(output.places):
        place = get_or_create_generated_place(db, place_data)
        link_place_to_trip(db, trip_id=trip.id, place=place, notes=place_data.reason, priority=index + 1)
        places_by_key[normalize_place_key(place.name, place.city, place.country)] = place
        places_by_key[normalize_place_key(place.name, None, None)] = place

    protected_items = [item for item in day.items if item.is_locked or item.is_booked]
    editable_items = [item for item in day.items if not item.is_locked and not item.is_booked]
    replacement_items, skipped_duplicates = filter_protected_duplicates(
        generated_items=output.items,
        protected_items=protected_items,
    )

    for item in editable_items:
        db.delete(item)
    db.flush()

    if output.day_title is not None:
        day.title = output.day_title
    if output.day_summary is not None:
        day.summary = output.day_summary

    used_sort_orders = {item.sort_order for item in protected_items}
    next_sort_order = max(used_sort_orders or {0}) + 1
    inserted_count = 0
    for index, item_data in enumerate(replacement_items):
        place = None
        if item_data.place_name:
            place_key = normalize_place_key(item_data.place_name, None, None)
            place = places_by_key.get(place_key)
            if not place:
                place = db.scalar(select(Place).where(func.lower(Place.name) == item_data.place_name.lower()))

        requested_sort_order = item_data.sort_order or index + 1
        if requested_sort_order in used_sort_orders:
            requested_sort_order = next_sort_order
        used_sort_orders.add(requested_sort_order)
        next_sort_order = max(next_sort_order, requested_sort_order + 1)

        db.add(
            ItineraryItem(
                itinerary_day_id=day.id,
                place_id=place.id if place else None,
                title=item_data.title,
                description=item_data.description,
                start_time=parse_time_string(item_data.start_time),
                end_time=parse_time_string(item_data.end_time),
                category=item_data.category,
                is_locked=False,
                is_booked=False,
                sort_order=requested_sort_order,
            )
        )
        inserted_count += 1

    planning_context = dict(trip.planning_context or {})
    regenerations = list(planning_context.get("regenerations", []))
    regenerations.append(
        {
            "scope": "day",
            "day_id": day.id,
            "day_number": day.day_number,
            "change_summary": output.change_summary,
            "warnings": output.warnings,
            "regenerated_at": utc_now().isoformat(),
        }
    )
    planning_context["regenerations"] = regenerations[-10:]
    trip.planning_context = planning_context
    trip.status = "Planning"
    trip.progress = max(trip.progress, 65)

    return {
        "day_id": day.id,
        "day_number": day.day_number,
        "places": len(output.places),
        "protected_items": len(protected_items),
        "editable_items_removed": len(editable_items),
        "items_inserted": inserted_count,
        "skipped_protected_duplicates": skipped_duplicates,
        "warnings": output.warnings,
        "change_summary": output.change_summary,
    }


def replace_generated_itinerary(
    db: Session,
    *,
    trip_id: str,
    generated_days: list[BuildTripItineraryDay],
    places_by_key: dict[tuple[str, str, str], Place],
) -> tuple[int, int, int]:
    existing_days = db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.trip_id == trip_id)
        .options(selectinload(ItineraryDay.items))
        .order_by(ItineraryDay.day_number.asc())
    ).all()

    preserved_by_day: dict[str, int] = {}
    preserved_count = 0
    for day in existing_days:
        preserved_for_day = 0
        for item in list(day.items):
            if item.is_locked or item.is_booked:
                preserved_count += 1
                preserved_for_day += 1
            else:
                db.delete(item)
        preserved_by_day[day.id] = preserved_for_day

    db.flush()

    for day in list(existing_days):
        if preserved_by_day.get(day.id, 0) == 0:
            db.delete(day)

    db.flush()

    days_by_number = {
        day.day_number: day
        for day in db.scalars(
            select(ItineraryDay)
            .where(ItineraryDay.trip_id == trip_id)
            .options(selectinload(ItineraryDay.items))
            .order_by(ItineraryDay.day_number.asc())
        ).all()
    }

    item_count = 0
    for generated_day in generated_days:
        day = days_by_number.get(generated_day.day_number)
        if not day:
            day = ItineraryDay(trip_id=trip_id, day_number=generated_day.day_number)
            db.add(day)
            db.flush()
            days_by_number[generated_day.day_number] = day

        day.title = generated_day.title
        day.summary = generated_day.summary
        preserved_sort_offset = len([item for item in day.items if item.is_locked or item.is_booked])

        for index, item_data in enumerate(generated_day.items):
            place = None
            if item_data.place_name:
                place_key = normalize_place_key(item_data.place_name, None, None)
                place = places_by_key.get(place_key)
                if not place:
                    place = db.scalar(select(Place).where(func.lower(Place.name) == item_data.place_name.lower()))

            db.add(
                ItineraryItem(
                    itinerary_day_id=day.id,
                    place_id=place.id if place else None,
                    title=item_data.title,
                    description=item_data.description,
                    start_time=parse_time_string(item_data.start_time),
                    end_time=parse_time_string(item_data.end_time),
                    category=item_data.category,
                    is_locked=False,
                    is_booked=False,
                    sort_order=item_data.sort_order or preserved_sort_offset + index + 1,
                )
            )
            item_count += 1

    return (len(generated_days), item_count, preserved_count)


def persist_build_trip_output(db: Session, *, trip: Trip, output: BuildTripOutput) -> dict:
    places_by_key: dict[tuple[str, str, str], Place] = {}
    for index, place_data in enumerate(output.places):
        place = get_or_create_generated_place(db, place_data)
        link_place_to_trip(db, trip_id=trip.id, place=place, notes=place_data.reason, priority=index + 1)
        places_by_key[normalize_place_key(place.name, place.city, place.country)] = place
        places_by_key[normalize_place_key(place.name, None, None)] = place

    days_count, items_count, preserved_count = replace_generated_itinerary(
        db,
        trip_id=trip.id,
        generated_days=output.itinerary,
        places_by_key=places_by_key,
    )

    existing_task_titles = {
        title.lower()
        for title in db.scalars(select(ChecklistItem.title).where(ChecklistItem.trip_id == trip.id)).all()
    }
    checklist_count = 0
    for item in output.checklist:
        if item.title.lower() in existing_task_titles:
            continue
        due_label = item.due_label or item.description
        db.add(
            ChecklistItem(
                trip_id=trip.id,
                title=item.title,
                due_label=due_label[:120] if due_label else None,
                priority=item.priority,
                is_completed=False,
            )
        )
        checklist_count += 1

    planning_context = dict(trip.planning_context or {})
    planning_context["build_trip"] = {
        "trip_summary": output.trip_summary,
        "assumptions": output.assumptions,
        "warnings": output.warnings,
        "budget": output.budget.model_dump() if output.budget else None,
        "last_built_at": utc_now().isoformat(),
    }
    trip.planning_context = planning_context
    trip.status = "Planning"
    trip.progress = max(trip.progress, 60)

    if output.budget and output.budget.total_estimate is not None:
        try:
            trip.budget_amount = Decimal(str(output.budget.total_estimate))
        except InvalidOperation:
            pass

    return {
        "places": len(output.places),
        "days": days_count,
        "items": items_count,
        "preserved_items": preserved_count,
        "checklist_items": checklist_count,
        "budget_estimated": bool(output.budget),
    }

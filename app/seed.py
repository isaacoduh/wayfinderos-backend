from datetime import date, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BETA_USER_EMAIL, BETA_USER_NAME
from app.db import SessionLocal
from app.models import AgentEvent, ChatMessage, ItineraryDay, ItineraryItem, Place, Trip, TripPlace, User


def get_or_create_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == BETA_USER_EMAIL))
    if user:
        return user

    user = User(display_name=BETA_USER_NAME, email=BETA_USER_EMAIL)
    db.add(user)
    db.flush()
    return user


def get_or_create_place(db: Session, *, name: str, category: str, city: str, country: str) -> Place:
    place = db.scalar(
        select(Place).where(
            Place.name == name,
            Place.city == city,
            Place.country == country,
        )
    )
    if place:
        return place

    place = Place(name=name, category=category, city=city, country=country)
    db.add(place)
    db.flush()
    return place


def get_or_create_trip(
    db: Session,
    user: User,
    *,
    title: str,
    destination: str,
    start_date: date,
    end_date: date,
    status: str,
    progress: int,
    budget_amount: Decimal,
) -> Trip:
    trip = db.scalar(select(Trip).where(Trip.user_id == user.id, Trip.title == title))
    if trip:
        return trip

    trip = Trip(
        user_id=user.id,
        title=title,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        status=status,
        progress=progress,
        budget_amount=budget_amount,
    )
    db.add(trip)
    db.flush()
    return trip


def add_trip_place_once(
    db: Session,
    *,
    trip: Trip,
    place: Place,
    status: str,
    notes: str | None = None,
    priority: int | None = None,
) -> None:
    existing = db.scalar(select(TripPlace).where(TripPlace.trip_id == trip.id, TripPlace.place_id == place.id))
    if existing:
        return

    db.add(TripPlace(trip_id=trip.id, place_id=place.id, status=status, notes=notes, priority=priority))


def add_message_once(db: Session, *, trip: Trip, role: str, content: str) -> None:
    existing = db.scalar(
        select(ChatMessage).where(
            ChatMessage.trip_id == trip.id,
            ChatMessage.role == role,
            ChatMessage.content == content,
        )
    )
    if not existing:
        db.add(ChatMessage(trip_id=trip.id, role=role, content=content))


def add_event_once(db: Session, *, trip: Trip, title: str, detail: str, status: str) -> None:
    existing = db.scalar(select(AgentEvent).where(AgentEvent.trip_id == trip.id, AgentEvent.title == title))
    if not existing:
        db.add(AgentEvent(trip_id=trip.id, title=title, detail=detail, status=status))


def add_itinerary_if_empty(db: Session, *, trip: Trip, days: list[dict]) -> None:
    existing_day = db.scalar(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))
    if existing_day:
        return

    for day_data in days:
        items = day_data.pop("items")
        day = ItineraryDay(trip_id=trip.id, **day_data)
        db.add(day)
        db.flush()

        for index, item_data in enumerate(items):
            place_name = item_data.pop("place_name", None)
            place = None
            if place_name:
                place = db.scalar(select(Place).where(Place.name == place_name))

            db.add(
                ItineraryItem(
                    itinerary_day_id=day.id,
                    place_id=place.id if place else None,
                    sort_order=index,
                    **item_data,
                )
            )


def seed() -> None:
    db = SessionLocal()
    try:
        user = get_or_create_user(db)

        tokyo = get_or_create_trip(
            db,
            user,
            title="Tokyo in spring",
            destination="Tokyo, Japan",
            start_date=date(2027, 4, 18),
            end_date=date(2027, 4, 27),
            status="Planning",
            progress=72,
            budget_amount=Decimal("4820.00"),
        )
        lisbon = get_or_create_trip(
            db,
            user,
            title="Lisbon food week",
            destination="Lisbon, Portugal",
            start_date=date(2026, 9, 5),
            end_date=date(2026, 9, 12),
            status="Ready",
            progress=94,
            budget_amount=Decimal("2340.00"),
        )

        tokyo_places = [
            ("Meiji Jingu", "Culture", "Tokyo", "Japan", "interested", "Ancient forest shrine", 1),
            ("Nezu Museum", "Museum", "Tokyo", "Japan", "suggested", "Art and garden", 2),
            ("Tsukiji Market", "Food", "Tokyo", "Japan", "interested", "Morning market crawl", 3),
            ("Yanaka Ginza", "Walk", "Tokyo", "Japan", "suggested", "Old Tokyo streets", 4),
            ("Sushi Ishiyama", "Dinner", "Tokyo", "Japan", "booked", "Reservation deposit paid", 5),
        ]
        for name, category, city, country, status, notes, priority in tokyo_places:
            place = get_or_create_place(db, name=name, category=category, city=city, country=country)
            add_trip_place_once(db, trip=tokyo, place=place, status=status, notes=notes, priority=priority)

        lisbon_places = [
            ("Time Out Market Lisboa", "Food", "Lisbon", "Portugal", "interested", "Easy first-night food hall", 1),
            ("Alfama", "Walk", "Lisbon", "Portugal", "booked", "Fado dinner area", 2),
            ("Pasteis de Belem", "Food", "Lisbon", "Portugal", "suggested", "Classic custard tart stop", 3),
            ("LX Factory", "Design", "Lisbon", "Portugal", "suggested", "Shops and casual restaurants", 4),
        ]
        for name, category, city, country, status, notes, priority in lisbon_places:
            place = get_or_create_place(db, name=name, category=category, city=city, country=country)
            add_trip_place_once(db, trip=lisbon, place=place, status=status, notes=notes, priority=priority)

        add_itinerary_if_empty(
            db,
            trip=tokyo,
            days=[
                {
                    "day_number": 1,
                    "date": date(2027, 4, 18),
                    "title": "Arrival and Shibuya",
                    "summary": "Easy pace after a long flight",
                    "items": [
                        {
                            "title": "Arrive at Haneda",
                            "description": "Terminal 3 transfer into Shibuya",
                            "start_time": time(15, 30),
                            "category": "Transit",
                            "is_locked": True,
                            "is_booked": True,
                        },
                        {
                            "title": "Check in near Cat Street",
                            "description": "Keep the first evening low-friction",
                            "start_time": time(17, 0),
                            "category": "Stay",
                            "is_locked": True,
                            "is_booked": True,
                        },
                        {
                            "title": "Dinner at Uobei Shibuya",
                            "description": "Quick sushi, no booking required",
                            "start_time": time(19, 30),
                            "category": "Food",
                            "is_locked": False,
                            "is_booked": False,
                        },
                    ],
                },
                {
                    "day_number": 2,
                    "date": date(2027, 4, 19),
                    "title": "Meiji and Harajuku",
                    "summary": "Architecture, gardens, and design",
                    "items": [
                        {
                            "place_name": "Meiji Jingu",
                            "title": "Meiji Jingu morning walk",
                            "description": "Quietest before 10",
                            "start_time": time(8, 30),
                            "category": "Place",
                            "is_locked": False,
                            "is_booked": False,
                        },
                        {
                            "place_name": "Nezu Museum",
                            "title": "Nezu Museum and garden",
                            "description": "Timed entry recommended",
                            "start_time": time(11, 0),
                            "category": "Culture",
                            "is_locked": False,
                            "is_booked": False,
                        },
                    ],
                },
            ],
        )

        add_itinerary_if_empty(
            db,
            trip=lisbon,
            days=[
                {
                    "day_number": 1,
                    "date": date(2026, 9, 5),
                    "title": "Baixa arrival",
                    "summary": "Settle in and keep dinner flexible",
                    "items": [
                        {
                            "place_name": "Time Out Market Lisboa",
                            "title": "Dinner at Time Out Market Lisboa",
                            "description": "Low-commitment first meal after arrival",
                            "start_time": time(19, 30),
                            "category": "Food",
                            "is_locked": False,
                            "is_booked": False,
                        }
                    ],
                },
                {
                    "day_number": 2,
                    "date": date(2026, 9, 6),
                    "title": "Alfama and Belem",
                    "summary": "Classic Lisbon food and old streets",
                    "items": [
                        {
                            "place_name": "Pasteis de Belem",
                            "title": "Pasteis de Belem",
                            "description": "Go before late-morning lines build",
                            "start_time": time(9, 30),
                            "category": "Food",
                            "is_locked": False,
                            "is_booked": False,
                        },
                        {
                            "place_name": "Alfama",
                            "title": "Fado dinner in Alfama",
                            "description": "Reservation held for evening slot",
                            "start_time": time(20, 0),
                            "category": "Dinner",
                            "is_locked": True,
                            "is_booked": True,
                        },
                    ],
                },
            ],
        )

        add_message_once(
            db,
            trip=tokyo,
            role="assistant",
            content="This Tokyo workspace is ready to refine. Ask me to adjust pacing, compare neighborhoods, or audit bookings.",
        )
        add_message_once(
            db,
            trip=lisbon,
            role="assistant",
            content="This Lisbon food week is saved in the shared beta workspace. Ask me to tune meals, walks, or booking readiness.",
        )

        add_event_once(
            db,
            trip=tokyo,
            title="Itinerary Optimizer completed",
            detail="Reduced transit by 48 minutes across days 2-4.",
            status="complete",
        )
        add_event_once(
            db,
            trip=tokyo,
            title="Restaurant Scout needs review",
            detail="Found 6 dinner options matching your budget.",
            status="review",
        )
        add_event_once(
            db,
            trip=lisbon,
            title="Booking Monitor is watching",
            detail="Fado reservation confirmation is saved for day 2.",
            status="active",
        )

        db.commit()
        print("Seeded shared beta user, Tokyo trip, and Lisbon trip.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()

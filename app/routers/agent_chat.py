from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.agent.prompts import BUILD_TRIP_SYSTEM_PROMPT, REGENERATE_DAY_SYSTEM_PROMPT, SYSTEM_PROMPT, TRIP_AWARE_SYSTEM_PROMPT
from app.agent.schemas import BuildTripOutput, RegenerateDayOutput, RegenerateDayRequest, TravelQuery, TripChatRequest
from app.config import openai_model
from app.db import SessionLocal, get_db
from app.logging import get_logger, log_event
from app.models import AgentRun, ChatMessage, ItineraryDay, ItineraryItem, Trip, TripPlace, utc_now
from app.services.agent_context import build_regenerate_day_prompt, build_structured_trip_prompt, build_trip_context_prompt
from app.services.auth import get_or_create_beta_user
from app.services.events import create_agent_event
from app.services.persistence import persist_build_trip_output, persist_regenerate_day_output
from app.services.planning_context import extract_trip_context_updates, merge_planning_context
from app.services.utils import get_event_error_message, get_response_text, ndjson, parse_json_object

router = APIRouter()
logger = get_logger("wayfinder.api")


@router.post("/trips/{trip_id}/chat")
def trip_chat(trip_id: str, body: TripChatRequest, db: Session = Depends(get_db)):
    text = body.message.strip()
    if not text:
        return StreamingResponse(
            iter([ndjson({"type": "error", "message": "Please enter a travel planning message."})]),
            media_type="application/x-ndjson",
        )

    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    extracted_context = extract_trip_context_updates(text)
    if extracted_context:
        trip.planning_context = merge_planning_context(trip.planning_context, extracted_context)

    user_message = ChatMessage(trip_id=trip.id, role="user", content=text)
    run = AgentRun(trip_id=trip.id, user_id=user.id, run_type="trip_chat", status="running", input_text=text)
    db.add_all([user_message, run])
    db.commit()
    log_event(
        logger,
        logging.INFO,
        "agent_run.started",
        agent_run_id=run.id,
        trip_id=trip.id,
        user_id=user.id,
        run_type="trip_chat",
    )

    run_id = run.id
    trip_id_for_stream = trip.id

    def stream_events():
        assistant_text = ""
        stream_db = SessionLocal()

        try:
            run_record = stream_db.get(AgentRun, run_id)
            trip_record = stream_db.scalar(
                select(Trip).where(Trip.id == trip_id_for_stream).options(selectinload(Trip.messages))
            )

            if not run_record or not trip_record:
                yield ndjson({"type": "error", "message": "Trip chat run could not be loaded."})
                return

            if not os.getenv("OPENAI_API_KEY"):
                run_record.status = "failed"
                run_record.error_message = "Wayfinder is not configured yet."
                run_record.finished_at = utc_now()
                stream_db.commit()
                yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
                return

            recent_messages = list(
                reversed(
                    stream_db.scalars(
                        select(ChatMessage)
                        .where(ChatMessage.trip_id == trip_id_for_stream)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(12)
                    ).all()
                )
            )
            days = stream_db.scalars(
                select(ItineraryDay)
                .where(ItineraryDay.trip_id == trip_id_for_stream)
                .options(selectinload(ItineraryDay.items))
                .order_by(ItineraryDay.day_number.asc())
            ).all()
            places = stream_db.scalars(
                select(TripPlace)
                .where(TripPlace.trip_id == trip_id_for_stream)
                .options(joinedload(TripPlace.place))
                .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
            ).unique().all()

            context_prompt = build_trip_context_prompt(trip_record, recent_messages, days, places)
            create_agent_event(stream_db, run=run_record, event_type="trip.context_loaded", payload={})
            stream_db.commit()
            yield ndjson({"type": "agent_event", "event": "trip.context_loaded", "payload": {}})

            if extracted_context.get("origin"):
                payload = {"origin": extracted_context["origin"]}
                create_agent_event(stream_db, run=run_record, event_type="trip.origin_detected", payload=payload)
                stream_db.commit()
                yield ndjson({"type": "agent_event", "event": "trip.origin_detected", "payload": payload})

            if extracted_context.get("constraints"):
                payload = {"constraints": extracted_context["constraints"]}
                create_agent_event(stream_db, run=run_record, event_type="trip.constraint_detected", payload=payload)
                stream_db.commit()
                yield ndjson({"type": "agent_event", "event": "trip.constraint_detected", "payload": payload})

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            stream = client.responses.create(
                model=openai_model(),
                input=[
                    {"role": "system", "content": TRIP_AWARE_SYSTEM_PROMPT},
                    {"role": "system", "content": context_prompt},
                    {"role": "user", "content": text},
                ],
                max_output_tokens=1600,
                stream=True,
            )

            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type in ("response.output_text.delta", "response.refusal.delta"):
                    delta = getattr(event, "delta", "")
                    if delta:
                        assistant_text += delta
                        yield ndjson({"type": "delta", "text": delta})

                elif event_type in ("response.failed", "response.incomplete", "error"):
                    raise RuntimeError(get_event_error_message(event))

            if assistant_text.strip():
                assistant_message = ChatMessage(
                    trip_id=trip_id_for_stream,
                    role="assistant",
                    content=assistant_text,
                )
                stream_db.add(assistant_message)

            run_record.status = "completed"
            run_record.output_summary = assistant_text[:500] if assistant_text else None
            run_record.finished_at = utc_now()
            create_agent_event(stream_db, run=run_record, event_type="assistant_message.created", payload={})
            stream_db.commit()
            log_event(
                logger,
                logging.INFO,
                "agent_run.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                run_type="trip_chat",
            )

            yield ndjson({"type": "agent_event", "event": "assistant_message.created", "payload": {}})
            yield ndjson({"type": "done"})

        except Exception as exc:
            run_record = stream_db.get(AgentRun, run_id)
            if run_record:
                run_record.status = "failed"
                run_record.error_message = str(exc)
                run_record.finished_at = utc_now()
                stream_db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "agent_run.failed",
                    agent_run_id=run_record.id,
                    trip_id=trip_id_for_stream,
                    run_type="trip_chat",
                    error=str(exc),
                )
            yield ndjson(
                {
                    "type": "error",
                    "message": "Wayfinder could not complete this request. Please try again.",
                }
            )
        finally:
            stream_db.close()

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/travel-query")
def travel_query(body: TravelQuery):
    def stream_events():
        if not body.query.strip():
            yield ndjson({"type": "error", "message": "Please enter a travel planning question."})
            return

        if not os.getenv("OPENAI_API_KEY"):
            yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
            return

        try:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            stream = client.responses.create(
                model=openai_model(),
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": body.query},
                ],
                max_output_tokens=1600,
                stream=True,
            )

            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type in ("response.output_text.delta", "response.refusal.delta"):
                    text = getattr(event, "delta", "")
                    if text:
                        yield ndjson({"type": "delta", "text": text})

                elif event_type in ("response.failed", "response.incomplete", "error"):
                    yield ndjson({"type": "error", "message": get_event_error_message(event)})
                    return

            yield ndjson({"type": "done"})

        except Exception:
            yield ndjson({
                "type": "error",
                "message": "Wayfinder could not complete this request. Please try again.",
            })

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

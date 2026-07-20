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


@router.post("/trips/{trip_id}/agent/build-trip")
def build_trip_agent(trip_id: str, db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    run = AgentRun(
        trip_id=trip.id,
        user_id=user.id,
        run_type="build_trip",
        status="running",
        input_text="Build My Trip",
    )
    db.add(run)
    db.flush()
    create_agent_event(db, run=run, event_type="agent_run.started", payload={"run_type": "build_trip"}, status="active")
    db.commit()
    log_event(
        logger,
        logging.INFO,
        "agent_run.started",
        agent_run_id=run.id,
        trip_id=trip.id,
        user_id=user.id,
        run_type="build_trip",
    )

    run_id = run.id
    trip_id_for_stream = trip.id

    def stream_events():
        stream_db = SessionLocal()

        def emit(run_record: AgentRun, event_type: str, payload: dict | None = None, status: str = "complete"):
            create_agent_event(stream_db, run=run_record, event_type=event_type, payload=payload or {}, status=status)
            stream_db.commit()
            log_event(
                logger,
                logging.INFO,
                event_type,
                agent_run_id=run_record.id,
                trip_id=run_record.trip_id,
                status=status,
                payload=payload or {},
            )
            return ndjson({"type": "agent_event", "event": event_type, "payload": payload or {}})

        try:
            run_record = stream_db.get(AgentRun, run_id)
            if not run_record:
                yield ndjson({"type": "error", "message": "Build My Trip run could not be loaded."})
                return

            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "agent_run.started",
                    "payload": {"agent_run_id": run_id},
                }
            )

            if not os.getenv("OPENAI_API_KEY"):
                run_record.status = "failed"
                run_record.error_message = "Wayfinder is not configured yet."
                run_record.finished_at = utc_now()
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="agent_run.failed",
                    payload={"message": "Wayfinder is not configured yet."},
                    status="failed",
                )
                stream_db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "agent_run.failed",
                    agent_run_id=run_record.id,
                    trip_id=run_record.trip_id,
                    run_type="build_trip",
                    error="Wayfinder is not configured yet.",
                )
                yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
                return

            trip_record = stream_db.scalar(
                select(Trip)
                .where(Trip.id == trip_id_for_stream)
                .options(selectinload(Trip.messages))
            )
            if not trip_record:
                raise RuntimeError("Trip could not be loaded.")

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

            yield emit(
                run_record,
                "trip.context_loaded",
                {
                    "messages": len(recent_messages),
                    "days": len(days),
                    "places": len(places),
                },
            )

            prompt = build_structured_trip_prompt(trip_record, recent_messages, days, places)
            yield emit(run_record, "build_trip.prompt_prepared", {"characters": len(prompt)})

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            log_event(
                logger,
                logging.INFO,
                "build_trip.llm_request.started",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                model=openai_model(),
            )
            response = client.responses.create(
                model=openai_model(),
                input=[
                    {"role": "system", "content": BUILD_TRIP_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=5000,
            )

            response_text = get_response_text(response)
            if not response_text.strip():
                raise RuntimeError("Build My Trip returned an empty response.")

            try:
                output = BuildTripOutput.model_validate(parse_json_object(response_text))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Build My Trip returned invalid structured output: {exc}") from exc

            yield emit(
                run_record,
                "build_trip.output_received",
                {
                    "days": len(output.itinerary),
                    "places": len(output.places),
                    "checklist_items": len(output.checklist),
                },
            )

            counts = persist_build_trip_output(stream_db, trip=trip_record, output=output)
            log_event(
                logger,
                logging.INFO,
                "build_trip.persistence.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                counts=counts,
            )
            create_agent_event(stream_db, run=run_record, event_type="place.recommended", payload={"count": counts["places"]})
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="itinerary.generated",
                payload={
                    "days": counts["days"],
                    "items": counts["items"],
                    "preserved_items": counts["preserved_items"],
                },
            )
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="booking_checklist.generated",
                payload={"count": counts["checklist_items"]},
            )
            if counts["budget_estimated"]:
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="budget.estimated",
                    payload={"total_estimate": output.budget.total_estimate if output.budget else None},
                )

            assistant_message = ChatMessage(
                trip_id=trip_id_for_stream,
                role="assistant",
                content=output.trip_summary,
            )
            stream_db.add(assistant_message)
            run_record.status = "completed"
            run_record.output_summary = output.trip_summary[:500]
            run_record.finished_at = utc_now()
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="agent_run.completed",
                payload=counts,
                status="complete",
            )
            stream_db.commit()
            log_event(
                logger,
                logging.INFO,
                "agent_run.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                run_type="build_trip",
                counts=counts,
            )

            yield ndjson({"type": "agent_event", "event": "place.recommended", "payload": {"count": counts["places"]}})
            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "itinerary.generated",
                    "payload": {
                        "days": counts["days"],
                        "items": counts["items"],
                        "preserved_items": counts["preserved_items"],
                    },
                }
            )
            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "booking_checklist.generated",
                    "payload": {"count": counts["checklist_items"]},
                }
            )
            if counts["budget_estimated"]:
                yield ndjson(
                    {
                        "type": "agent_event",
                        "event": "budget.estimated",
                        "payload": {"total_estimate": output.budget.total_estimate if output.budget else None},
                    }
                )
            yield ndjson({"type": "delta", "text": output.trip_summary})
            yield ndjson({"type": "done", "agent_run_id": run_id})

        except Exception as exc:
            stream_db.rollback()
            run_record = stream_db.get(AgentRun, run_id)
            if run_record:
                run_record.status = "failed"
                run_record.error_message = str(exc)
                run_record.finished_at = utc_now()
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="agent_run.failed",
                    payload={"message": str(exc)[:500]},
                    status="failed",
                )
                stream_db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "agent_run.failed",
                    agent_run_id=run_record.id,
                    trip_id=run_record.trip_id,
                    run_type="build_trip",
                    error=str(exc),
                )
            yield ndjson(
                {
                    "type": "error",
                    "message": "Wayfinder could not build this trip. Please try again.",
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



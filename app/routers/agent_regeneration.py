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


@router.post("/trips/{trip_id}/agent/regenerate-day/{day_id}")
def regenerate_day_agent(
    trip_id: str,
    day_id: str,
    body: RegenerateDayRequest,
    db: Session = Depends(get_db),
):
    instruction = body.instruction.strip()
    if not instruction:
        return StreamingResponse(
            iter([ndjson({"type": "error", "message": "Please describe what Wayfinder should change."})]),
            media_type="application/x-ndjson",
        )

    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    day = db.scalar(select(ItineraryDay).where(ItineraryDay.id == day_id, ItineraryDay.trip_id == trip.id))
    if not day:
        raise HTTPException(status_code=404, detail="Itinerary day not found")

    run = AgentRun(
        trip_id=trip.id,
        user_id=user.id,
        run_type="regenerate_day",
        status="running",
        input_text=instruction,
    )
    db.add(run)
    db.flush()
    create_agent_event(
        db,
        run=run,
        event_type="agent_run.started",
        payload={"run_type": "regenerate_day", "day_id": day.id, "day_number": day.day_number},
        status="active",
    )
    db.commit()
    log_event(
        logger,
        logging.INFO,
        "agent_run.started",
        agent_run_id=run.id,
        trip_id=trip.id,
        user_id=user.id,
        run_type="regenerate_day",
        day_id=day.id,
        day_number=day.day_number,
    )

    run_id = run.id
    trip_id_for_stream = trip.id
    day_id_for_stream = day.id

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
                run_type="regenerate_day",
                status=status,
                payload=payload or {},
            )
            return ndjson({"type": "agent_event", "event": event_type, "payload": payload or {}})

        try:
            run_record = stream_db.get(AgentRun, run_id)
            if not run_record:
                yield ndjson({"type": "error", "message": "Regeneration run could not be loaded."})
                return

            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "agent_run.started",
                    "payload": {"agent_run_id": run_id, "day_id": day_id_for_stream},
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
                    run_type="regenerate_day",
                    error="Wayfinder is not configured yet.",
                )
                yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
                return

            trip_record = stream_db.scalar(select(Trip).where(Trip.id == trip_id_for_stream))
            selected_day = stream_db.scalar(
                select(ItineraryDay)
                .where(ItineraryDay.id == day_id_for_stream, ItineraryDay.trip_id == trip_id_for_stream)
                .options(selectinload(ItineraryDay.items).selectinload(ItineraryItem.place))
            )
            if not trip_record or not selected_day:
                raise RuntimeError("Selected trip day could not be loaded.")

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

            protected_count = len([item for item in selected_day.items if item.is_locked or item.is_booked])
            editable_count = len([item for item in selected_day.items if not item.is_locked and not item.is_booked])

            yield emit(
                run_record,
                "trip.context_loaded",
                {
                    "messages": len(recent_messages),
                    "days": len(days),
                    "places": len(places),
                },
            )
            yield emit(
                run_record,
                "regenerate_day.scope_loaded",
                {
                    "day_id": selected_day.id,
                    "day_number": selected_day.day_number,
                    "items": len(selected_day.items),
                },
            )
            yield emit(
                run_record,
                "regenerate_day.protected_items_identified",
                {
                    "protected_items": protected_count,
                    "editable_items": editable_count,
                },
            )

            prompt = build_regenerate_day_prompt(
                trip=trip_record,
                recent_messages=recent_messages,
                days=days,
                places=places,
                day=selected_day,
                instruction=instruction,
            )
            yield emit(run_record, "regenerate_day.prompt_prepared", {"characters": len(prompt)})

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            log_event(
                logger,
                logging.INFO,
                "regenerate_day.llm_request.started",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                day_id=day_id_for_stream,
                protected_items=protected_count,
                editable_items=editable_count,
                model=openai_model(),
            )
            response = client.responses.create(
                model=openai_model(),
                input=[
                    {"role": "system", "content": REGENERATE_DAY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=3500,
            )

            response_text = get_response_text(response)
            if not response_text.strip():
                raise RuntimeError("Day regeneration returned an empty response.")

            try:
                output = RegenerateDayOutput.model_validate(parse_json_object(response_text))
            except (ValidationError, json.JSONDecodeError) as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "regenerate_day.output_parse.failed",
                    agent_run_id=run_record.id,
                    trip_id=trip_id_for_stream,
                    day_id=day_id_for_stream,
                    error=str(exc),
                )
                raise RuntimeError(f"Day regeneration returned invalid structured output: {exc}") from exc

            log_event(
                logger,
                logging.INFO,
                "regenerate_day.output_parse.succeeded",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                day_id=day_id_for_stream,
                items=len(output.items),
                places=len(output.places),
                warnings=len(output.warnings),
            )
            yield emit(
                run_record,
                "regenerate_day.output_received",
                {
                    "items": len(output.items),
                    "places": len(output.places),
                    "warnings": len(output.warnings),
                },
            )

            counts = persist_regenerate_day_output(stream_db, trip=trip_record, day=selected_day, output=output)
            log_event(
                logger,
                logging.INFO,
                "regenerate_day.persistence.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                day_id=day_id_for_stream,
                counts=counts,
            )
            create_agent_event(stream_db, run=run_record, event_type="place.recommended", payload={"count": counts["places"]})
            create_agent_event(stream_db, run=run_record, event_type="regenerate_day.items_replaced", payload=counts)
            if output.warnings:
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="regenerate_day.warning",
                    payload={"warnings": output.warnings},
                    status="review",
                )

            assistant_message = ChatMessage(
                trip_id=trip_id_for_stream,
                role="assistant",
                content=output.change_summary,
            )
            stream_db.add(assistant_message)
            run_record.status = "completed"
            run_record.output_summary = output.change_summary[:500]
            run_record.finished_at = utc_now()
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="regenerate_day.completed",
                payload=counts,
                status="complete",
            )
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
                run_type="regenerate_day",
                counts=counts,
            )

            yield ndjson({"type": "agent_event", "event": "place.recommended", "payload": {"count": counts["places"]}})
            yield ndjson({"type": "agent_event", "event": "regenerate_day.items_replaced", "payload": counts})
            if output.warnings:
                yield ndjson(
                    {
                        "type": "agent_event",
                        "event": "regenerate_day.warning",
                        "payload": {"warnings": output.warnings},
                    }
                )
            yield ndjson({"type": "agent_event", "event": "regenerate_day.completed", "payload": counts})
            yield ndjson({"type": "delta", "text": output.change_summary})
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
                    run_type="regenerate_day",
                    error=str(exc),
                )
            yield ndjson(
                {
                    "type": "error",
                    "message": "Wayfinder could not regenerate this day. Please try again.",
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



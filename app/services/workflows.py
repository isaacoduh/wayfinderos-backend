from __future__ import annotations

import json
import logging
import os

from openai import OpenAI
from pydantic import ValidationError
from rq import get_current_job
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.agent.prompts import BUILD_TRIP_SYSTEM_PROMPT, REGENERATE_DAY_SYSTEM_PROMPT
from app.agent.schemas import BuildTripOutput, RegenerateDayOutput
from app.config import openai_model
from app.db import SessionLocal
from app.logging import get_logger, log_event
from app.models import AgentRun, ChatMessage, ItineraryDay, ItineraryItem, Trip, TripPlace, utc_now
from app.services.agent_context import build_regenerate_day_prompt, build_structured_trip_prompt
from app.services.events import create_agent_event
from app.services.persistence import persist_build_trip_output, persist_regenerate_day_output
from app.services.utils import get_response_text, parse_json_object

logger = get_logger("wayfinder.worker")


def emit_agent_event(db, run: AgentRun, event_type: str, payload: dict | None = None, status: str = "complete") -> None:
    create_agent_event(db, run=run, event_type=event_type, payload=payload or {}, status=status)
    db.commit()
    log_event(
        logger,
        logging.INFO,
        event_type,
        agent_run_id=run.id,
        trip_id=run.trip_id,
        run_type=run.run_type,
        status=status,
        payload=payload or {},
    )


def mark_run_failed(db, run: AgentRun, message: str) -> None:
    run.status = "failed"
    run.error_message = message
    run.finished_at = utc_now()
    create_agent_event(
        db,
        run=run,
        event_type="agent_run.failed",
        payload={"message": message[:500]},
        status="failed",
    )
    db.commit()
    log_event(
        logger,
        logging.ERROR,
        "worker_job.failed",
        agent_run_id=run.id,
        trip_id=run.trip_id,
        run_type=run.run_type,
        error=message,
    )


def handle_workflow_exception(db, run: AgentRun, message: str) -> None:
    job = get_current_job()
    retries_left = getattr(job, "retries_left", 0) if job else 0
    if retries_left and retries_left > 0:
        run.status = "queued"
        run.error_message = message
        create_agent_event(
            db,
            run=run,
            event_type="agent_run.retrying",
            payload={"message": message[:500], "retries_left": retries_left},
            status="active",
        )
        db.commit()
        log_event(
            logger,
            logging.WARNING,
            "worker_job.retrying",
            agent_run_id=run.id,
            trip_id=run.trip_id,
            run_type=run.run_type,
            retries_left=retries_left,
            error=message,
        )
        return

    mark_run_failed(db, run, message)


def load_trip_workflow_context(db, trip_id: str):
    trip = db.scalar(select(Trip).where(Trip.id == trip_id).options(selectinload(Trip.messages)))
    if not trip:
        raise RuntimeError("Trip could not be loaded.")

    recent_messages = list(
        reversed(
            db.scalars(
                select(ChatMessage)
                .where(ChatMessage.trip_id == trip_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(12)
            ).all()
        )
    )
    days = db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.trip_id == trip_id)
        .options(selectinload(ItineraryDay.items))
        .order_by(ItineraryDay.day_number.asc())
    ).all()
    places = db.scalars(
        select(TripPlace)
        .where(TripPlace.trip_id == trip_id)
        .options(joinedload(TripPlace.place))
        .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
    ).unique().all()

    return trip, recent_messages, days, places


def start_run(db, run: AgentRun) -> None:
    run.status = "running"
    run.started_at = utc_now()
    db.commit()
    emit_agent_event(db, run, "agent_run.started", {"run_type": run.run_type}, status="active")
    log_event(
        logger,
        logging.INFO,
        "worker_job.started",
        agent_run_id=run.id,
        trip_id=run.trip_id,
        run_type=run.run_type,
        job_id=run.job_id,
    )


def run_build_trip_workflow(agent_run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, agent_run_id)
        if not run:
            raise RuntimeError("Build My Trip run could not be loaded.")

        start_run(db, run)

        if not os.getenv("OPENAI_API_KEY"):
            mark_run_failed(db, run, "Wayfinder is not configured yet.")
            return

        trip, recent_messages, days, places = load_trip_workflow_context(db, run.trip_id)
        emit_agent_event(
            db,
            run,
            "trip.context_loaded",
            {"messages": len(recent_messages), "days": len(days), "places": len(places)},
        )

        prompt = build_structured_trip_prompt(trip, recent_messages, days, places)
        emit_agent_event(db, run, "build_trip.prompt_prepared", {"characters": len(prompt)})

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        log_event(
            logger,
            logging.INFO,
            "build_trip.llm_request.started",
            agent_run_id=run.id,
            trip_id=run.trip_id,
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

        emit_agent_event(
            db,
            run,
            "build_trip.output_received",
            {"days": len(output.itinerary), "places": len(output.places), "checklist_items": len(output.checklist)},
        )

        counts = persist_build_trip_output(db, trip=trip, output=output)
        create_agent_event(db, run=run, event_type="build_trip.persistence.completed", payload=counts)
        create_agent_event(db, run=run, event_type="place.recommended", payload={"count": counts["places"]})
        create_agent_event(
            db,
            run=run,
            event_type="itinerary.generated",
            payload={"days": counts["days"], "items": counts["items"], "preserved_items": counts["preserved_items"]},
        )
        create_agent_event(
            db,
            run=run,
            event_type="booking_checklist.generated",
            payload={"count": counts["checklist_items"]},
        )
        if counts["budget_estimated"]:
            create_agent_event(
                db,
                run=run,
                event_type="budget.estimated",
                payload={"total_estimate": output.budget.total_estimate if output.budget else None},
            )

        db.add(ChatMessage(trip_id=run.trip_id, role="assistant", content=output.trip_summary))
        run.status = "completed"
        run.output_summary = output.trip_summary[:500]
        run.finished_at = utc_now()
        create_agent_event(db, run=run, event_type="agent_run.completed", payload=counts, status="complete")
        db.commit()
        log_event(
            logger,
            logging.INFO,
            "worker_job.completed",
            agent_run_id=run.id,
            trip_id=run.trip_id,
            run_type=run.run_type,
            counts=counts,
        )
    except Exception as exc:
        db.rollback()
        run = db.get(AgentRun, agent_run_id)
        if run:
            handle_workflow_exception(db, run, str(exc))
        raise
    finally:
        db.close()


def run_regenerate_day_workflow(agent_run_id: str, day_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, agent_run_id)
        if not run:
            raise RuntimeError("Regeneration run could not be loaded.")

        start_run(db, run)

        if not os.getenv("OPENAI_API_KEY"):
            mark_run_failed(db, run, "Wayfinder is not configured yet.")
            return

        trip, recent_messages, days, places = load_trip_workflow_context(db, run.trip_id)
        selected_day = db.scalar(
            select(ItineraryDay)
            .where(ItineraryDay.id == day_id, ItineraryDay.trip_id == run.trip_id)
            .options(selectinload(ItineraryDay.items).selectinload(ItineraryItem.place))
        )
        if not selected_day:
            raise RuntimeError("Selected trip day could not be loaded.")

        protected_count = len([item for item in selected_day.items if item.is_locked or item.is_booked])
        editable_count = len([item for item in selected_day.items if not item.is_locked and not item.is_booked])

        emit_agent_event(
            db,
            run,
            "trip.context_loaded",
            {"messages": len(recent_messages), "days": len(days), "places": len(places)},
        )
        emit_agent_event(
            db,
            run,
            "regenerate_day.scope_loaded",
            {"day_id": selected_day.id, "day_number": selected_day.day_number, "items": len(selected_day.items)},
        )
        emit_agent_event(
            db,
            run,
            "regenerate_day.protected_items_identified",
            {"protected_items": protected_count, "editable_items": editable_count},
        )

        prompt = build_regenerate_day_prompt(
            trip=trip,
            recent_messages=recent_messages,
            days=days,
            places=places,
            day=selected_day,
            instruction=run.input_text,
        )
        emit_agent_event(db, run, "regenerate_day.prompt_prepared", {"characters": len(prompt)})

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        log_event(
            logger,
            logging.INFO,
            "regenerate_day.llm_request.started",
            agent_run_id=run.id,
            trip_id=run.trip_id,
            day_id=day_id,
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
            raise RuntimeError(f"Day regeneration returned invalid structured output: {exc}") from exc

        emit_agent_event(
            db,
            run,
            "regenerate_day.output_received",
            {"items": len(output.items), "places": len(output.places), "warnings": len(output.warnings)},
        )

        counts = persist_regenerate_day_output(db, trip=trip, day=selected_day, output=output)
        create_agent_event(db, run=run, event_type="regenerate_day.persistence.completed", payload=counts)
        create_agent_event(db, run=run, event_type="place.recommended", payload={"count": counts["places"]})
        create_agent_event(db, run=run, event_type="regenerate_day.items_replaced", payload=counts)
        if output.warnings:
            create_agent_event(
                db,
                run=run,
                event_type="regenerate_day.warning",
                payload={"warnings": output.warnings},
                status="review",
            )

        db.add(ChatMessage(trip_id=run.trip_id, role="assistant", content=output.change_summary))
        run.status = "completed"
        run.output_summary = output.change_summary[:500]
        run.finished_at = utc_now()
        create_agent_event(db, run=run, event_type="regenerate_day.completed", payload=counts, status="complete")
        create_agent_event(db, run=run, event_type="agent_run.completed", payload=counts, status="complete")
        db.commit()
        log_event(
            logger,
            logging.INFO,
            "worker_job.completed",
            agent_run_id=run.id,
            trip_id=run.trip_id,
            run_type=run.run_type,
            counts=counts,
        )
    except Exception as exc:
        db.rollback()
        run = db.get(AgentRun, agent_run_id)
        if run:
            handle_workflow_exception(db, run, str(exc))
        raise
    finally:
        db.close()

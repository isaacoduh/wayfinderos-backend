from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.schemas import RegenerateDayRequest
from app.db import get_db
from app.logging import get_logger, log_event
from app.models import AgentRun, ItineraryDay, Trip, User, utc_now
from app.queue import get_workflow_queue, workflow_retry
from app.services.auth import get_current_user
from app.services.events import create_agent_event
from app.services.workflows import run_regenerate_day_workflow

router = APIRouter()
logger = get_logger("wayfinder.api")


@router.post("/trips/{trip_id}/agent/regenerate-day/{day_id}")
def regenerate_day_agent(
    trip_id: str,
    day_id: str,
    body: RegenerateDayRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="Please describe what Wayfinder should change.")

    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    day = db.scalar(select(ItineraryDay).where(ItineraryDay.id == day_id, ItineraryDay.trip_id == trip.id))
    if not day:
        raise HTTPException(status_code=404, detail="Itinerary day not found")

    if idempotency_key:
        existing_run = db.scalar(
            select(AgentRun)
            .where(
                AgentRun.trip_id == trip.id,
                AgentRun.user_id == user.id,
                AgentRun.run_type == "regenerate_day",
                AgentRun.idempotency_key == idempotency_key,
            )
            .order_by(AgentRun.started_at.desc())
        )
        if existing_run:
            log_event(
                logger,
                logging.INFO,
                "workflow_enqueue.duplicate_idempotency_key",
                agent_run_id=existing_run.id,
                trip_id=trip.id,
                user_id=user.id,
                run_type="regenerate_day",
                day_id=day.id,
            )
            return {"agent_run_id": existing_run.id, "status": existing_run.status}

    run = AgentRun(
        trip_id=trip.id,
        user_id=user.id,
        run_type="regenerate_day",
        status="queued",
        input_text=instruction,
        idempotency_key=idempotency_key,
        queued_at=utc_now(),
    )
    db.add(run)
    db.flush()
    create_agent_event(
        db,
        run=run,
        event_type="agent_run.queued",
        payload={"run_type": "regenerate_day", "day_id": day.id, "day_number": day.day_number},
        status="active",
    )
    db.commit()

    try:
        job = get_workflow_queue().enqueue(run_regenerate_day_workflow, run.id, day.id, retry=workflow_retry())
    except Exception as exc:
        run.status = "failed"
        run.error_message = "Wayfinder could not enqueue this workflow."
        run.finished_at = utc_now()
        create_agent_event(
            db,
            run=run,
            event_type="agent_run.failed",
            payload={"message": run.error_message},
            status="failed",
        )
        db.commit()
        log_event(
            logger,
            logging.ERROR,
            "workflow_enqueue.failed",
            agent_run_id=run.id,
            trip_id=trip.id,
            user_id=user.id,
            run_type="regenerate_day",
            day_id=day.id,
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail="Wayfinder could not enqueue this workflow.") from exc

    run.job_id = job.id
    db.commit()
    log_event(
        logger,
        logging.INFO,
        "workflow_enqueue.completed",
        agent_run_id=run.id,
        trip_id=trip.id,
        user_id=user.id,
        run_type="regenerate_day",
        day_id=day.id,
        job_id=job.id,
    )
    return {"agent_run_id": run.id, "status": run.status}

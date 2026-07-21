from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging import get_logger, log_event
from app.models import AgentRun, Trip, User, utc_now
from app.queue import get_workflow_queue, workflow_retry
from app.services.auth import get_current_user
from app.services.events import create_agent_event
from app.services.workflows import run_build_trip_workflow

router = APIRouter()
logger = get_logger("wayfinder.api")


@router.post("/trips/{trip_id}/agent/build-trip")
def build_trip_agent(
    trip_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if idempotency_key:
        existing_run = db.scalar(
            select(AgentRun)
            .where(
                AgentRun.trip_id == trip.id,
                AgentRun.user_id == user.id,
                AgentRun.run_type == "build_trip",
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
                run_type="build_trip",
            )
            return {"agent_run_id": existing_run.id, "status": existing_run.status}

    run = AgentRun(
        trip_id=trip.id,
        user_id=user.id,
        run_type="build_trip",
        status="queued",
        input_text="Build My Trip",
        idempotency_key=idempotency_key,
        queued_at=utc_now(),
    )
    db.add(run)
    db.flush()
    create_agent_event(db, run=run, event_type="agent_run.queued", payload={"run_type": "build_trip"}, status="active")
    db.commit()

    try:
        job = get_workflow_queue().enqueue(run_build_trip_workflow, run.id, retry=workflow_retry())
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
            run_type="build_trip",
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
        run_type="build_trip",
        job_id=job.id,
    )
    return {"agent_run_id": run.id, "status": run.status}

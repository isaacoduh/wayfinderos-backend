from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import AgentEvent, AgentRun


def create_agent_event(
    db: Session,
    *,
    run: AgentRun,
    event_type: str,
    payload: dict | None = None,
    status: str = "complete",
) -> AgentEvent:
    event = AgentEvent(
        trip_id=run.trip_id,
        agent_run_id=run.id,
        event_type=event_type,
        payload=payload or {},
        title=event_type,
        detail=json.dumps(payload or {}),
        status=status,
    )
    db.add(event)
    return event

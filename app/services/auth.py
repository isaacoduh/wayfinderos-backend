from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BETA_USER_EMAIL, BETA_USER_NAME
from app.models import Trip, User


def get_or_create_beta_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == BETA_USER_EMAIL))
    if user:
        return user

    user = User(display_name=BETA_USER_NAME, email=BETA_USER_EMAIL)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_beta_trip_or_404(db: Session, trip_id: str) -> Trip:
    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def apply_patch_values(model, patch: BaseModel, fields: Iterable[str]) -> None:
    values = patch.model_dump(exclude_unset=True)
    for field in fields:
        if field in values:
            setattr(model, field, values[field])

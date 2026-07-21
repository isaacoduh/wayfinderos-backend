from __future__ import annotations

import logging
from collections.abc import Iterable

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import (
    AUTH_DEV_BYPASS,
    BETA_USER_EMAIL,
    BETA_USER_NAME,
    CLERK_AUTHORIZED_PARTIES,
    CLERK_JWT_KEY,
    CLERK_SECRET_KEY,
)
from app.db import get_db
from app.logging import get_logger, log_event
from app.models import Trip, User

logger = get_logger("wayfinder.auth")


def get_or_create_beta_user(db: Session) -> User:
    user = db.scalar(
        select(User).where(
            User.auth_provider == "dev",
            User.auth_provider_user_id == "shared-beta",
        )
    )
    if not user:
        user = db.scalar(select(User).where(User.email == BETA_USER_EMAIL))
    if user:
        changed = False
        if user.auth_provider != "dev":
            user.auth_provider = "dev"
            changed = True
        if user.auth_provider_user_id != "shared-beta":
            user.auth_provider_user_id = "shared-beta"
            changed = True
        if changed:
            db.commit()
            db.refresh(user)
        return user

    user = User(
        display_name=BETA_USER_NAME,
        email=BETA_USER_EMAIL,
        auth_provider="dev",
        auth_provider_user_id="shared-beta",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_owned_trip_or_404(db: Session, trip_id: str, user: User) -> Trip:
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def _claim(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _email_from_payload(payload: dict) -> str | None:
    email = _claim(payload, "email", "email_address")
    if email:
        return email

    claims = payload.get("claims")
    if isinstance(claims, dict):
        return _claim(claims, "email", "email_address")

    return None


def _display_name_from_payload(payload: dict, email: str | None) -> str:
    name = _claim(payload, "name", "full_name", "given_name", "username")
    if name:
        return name[:120]
    if email:
        return email.split("@", 1)[0][:120]
    return "Wayfinder user"


def _avatar_url_from_payload(payload: dict) -> str | None:
    return _claim(payload, "picture", "image_url", "avatar_url")


def _available_email(db: Session, email: str | None, user_id: str | None = None) -> str | None:
    if not email:
        return None

    existing_user_id = db.scalar(select(User.id).where(User.email == email))
    if existing_user_id and existing_user_id != user_id:
        return None

    return email


def get_or_create_auth_user(db: Session, *, provider: str, provider_user_id: str, payload: dict) -> User:
    user = db.scalar(
        select(User).where(
            User.auth_provider == provider,
            User.auth_provider_user_id == provider_user_id,
        )
    )

    email = _email_from_payload(payload)
    display_name = _display_name_from_payload(payload, email)
    avatar_url = _avatar_url_from_payload(payload)

    if not user:
        safe_email = _available_email(db, email)
        user = User(
            display_name=display_name,
            email=safe_email,
            auth_provider=provider,
            auth_provider_user_id=provider_user_id,
            avatar_url=avatar_url,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(
                select(User).where(
                    User.auth_provider == provider,
                    User.auth_provider_user_id == provider_user_id,
                )
            )
            if user:
                return user
            log_event(logger, logging.ERROR, "auth.user_create_integrity_failed", provider=provider)
            raise HTTPException(status_code=500, detail="Could not create authenticated user.")
        db.refresh(user)
        return user

    changed = False
    for attr, value in (
        ("email", _available_email(db, email, user.id)),
        ("display_name", display_name),
        ("avatar_url", avatar_url),
    ):
        if value and getattr(user, attr) != value:
            setattr(user, attr, value)
            changed = True

    if changed:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            log_event(logger, logging.ERROR, "auth.user_update_integrity_failed", user_id=user.id)
            db.refresh(user)
            return user
        db.refresh(user)

    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if AUTH_DEV_BYPASS:
        log_event(logger, logging.WARNING, "auth.dev_bypass.used", path=request.url.path)
        return get_or_create_beta_user(db)

    if not CLERK_SECRET_KEY and not CLERK_JWT_KEY:
        log_event(logger, logging.ERROR, "auth.not_configured", path=request.url.path)
        raise HTTPException(status_code=503, detail="Authentication is not configured.")

    try:
        request_state = authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=CLERK_SECRET_KEY,
                jwt_key=CLERK_JWT_KEY,
                authorized_parties=CLERK_AUTHORIZED_PARTIES or None,
                accepts_token=["session_token"],
            ),
        )
    except Exception as exc:
        log_event(logger, logging.WARNING, "auth.clerk_verification.failed", error=str(exc))
        raise HTTPException(status_code=401, detail="Unauthorized") from exc

    if not request_state.is_signed_in:
        detail = request_state.reason.name if getattr(request_state, "reason", None) else "Unauthorized"
        log_event(logger, logging.WARNING, "auth.clerk_verification.signed_out", reason=detail, path=request.url.path)
        raise HTTPException(status_code=401, detail=detail)

    payload = dict(request_state.payload or {})
    provider_user_id = _claim(payload, "sub")
    if not provider_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return get_or_create_auth_user(db, provider="clerk", provider_user_id=provider_user_id, payload=payload)


def apply_patch_values(model, patch: BaseModel, fields: Iterable[str]) -> None:
    values = patch.model_dump(exclude_unset=True)
    for field in fields:
        if field in values:
            setattr(model, field, values[field])

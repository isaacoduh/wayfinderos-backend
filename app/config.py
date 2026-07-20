from __future__ import annotations

import os

BETA_USER_EMAIL = os.getenv("BETA_USER_EMAIL", "beta@wayfinder.test")
BETA_USER_NAME = os.getenv("BETA_USER_NAME", "Beta Tester")
CHAT_ROLES = {"user", "assistant", "system"}
TRIP_PLACE_STATUSES = {"suggested", "interested", "booked", "skipped"}
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
DEFAULT_OPENAI_MODEL = "gpt-5.2"


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

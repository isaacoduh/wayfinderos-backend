from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

BETA_USER_EMAIL = os.getenv("BETA_USER_EMAIL", "beta@wayfinder.test")
BETA_USER_NAME = os.getenv("BETA_USER_NAME", "Beta Tester")
CHAT_ROLES = {"user", "assistant", "system"}
TRIP_PLACE_STATUSES = {"suggested", "interested", "booked", "skipped"}
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
DEFAULT_OPENAI_MODEL = "gpt-5.2"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
WORKFLOW_QUEUE_NAME = os.getenv("WORKFLOW_QUEUE_NAME", "wayfinder")
AUTH_DEV_BYPASS = os.getenv("AUTH_DEV_BYPASS", "").lower() in {"1", "true", "yes"}
LOG_REQUESTS = os.getenv("LOG_REQUESTS", "").lower() in {"1", "true", "yes"}
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_JWT_KEY = os.getenv("CLERK_JWT_KEY")
if CLERK_JWT_KEY and "\\n" in CLERK_JWT_KEY:
    CLERK_JWT_KEY = CLERK_JWT_KEY.replace("\\n", "\n")
if CLERK_JWT_KEY and not CLERK_JWT_KEY.strip().endswith("-----END PUBLIC KEY-----"):
    CLERK_JWT_KEY = None
CLERK_AUTHORIZED_PARTIES = [
    origin.strip()
    for origin in os.getenv("CLERK_AUTHORIZED_PARTIES", FRONTEND_ORIGIN).split(",")
    if origin.strip() and origin.strip() != "*"
]


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

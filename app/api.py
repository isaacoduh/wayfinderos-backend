from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import FRONTEND_ORIGIN
from app.logging import configure_logging, get_logger, log_event
from app.routers import agent, trips

configure_logging()
logger = get_logger("wayfinder.api")

app = FastAPI(title="Wayfinder OS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def structured_request_logging(request, call_next):
    start = time.perf_counter()
    response = None

    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=getattr(response, "status_code", 500),
            duration_ms=duration_ms,
        )


@app.get("/health")
def health():
    return {"status": "ok", "version": "v0.7"}


app.include_router(trips.router)
app.include_router(agent.router)

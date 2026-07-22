from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import FRONTEND_ORIGIN, LOG_REQUESTS
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
    except Exception:
        logger.exception(
            "request.failed",
            extra={
                "structured": {
                    "method": request.method,
                    "path": request.url.path,
                }
            },
        )
        raise
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status_code = getattr(response, "status_code", 500)
        if LOG_REQUESTS or status_code >= 400:
            log_event(
                logger,
                logging.INFO,
                "request.completed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )


@app.get("/health")
def health():
    return {"status": "ok", "version": "v0.8"}


app.include_router(trips.router)
app.include_router(agent.router)

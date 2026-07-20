from __future__ import annotations

import os

from redis import Redis
from rq import SimpleWorker, Worker

from app.config import REDIS_URL, WORKFLOW_QUEUE_NAME
from app.logging import configure_logging


if __name__ == "__main__":
    configure_logging()
    connection = Redis.from_url(REDIS_URL)
    worker_class = Worker if os.getenv("RQ_WORKER_CLASS") == "forking" else SimpleWorker
    worker_class([WORKFLOW_QUEUE_NAME], connection=connection).work()

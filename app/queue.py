from __future__ import annotations

from redis import Redis
from rq import Queue, Retry

from app.config import REDIS_URL, WORKFLOW_QUEUE_NAME


def get_redis_connection() -> Redis:
    return Redis.from_url(REDIS_URL)


def get_workflow_queue() -> Queue:
    return Queue(WORKFLOW_QUEUE_NAME, connection=get_redis_connection())


def workflow_retry() -> Retry:
    return Retry(max=1, interval=[10])

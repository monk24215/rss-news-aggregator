"""Worker entrypoint.

Run with:  python -m app.worker

The worker consumes jobs from Redis and executes them (§43.1). It listens on QUEUE_WORKER.
In Section 1 the only job is the heartbeat. Later phases register ingestion, clustering,
scoring, and AI tasks in `functions`.
"""

import logging

from arq import run_worker

from app.queue import QUEUE_WORKER, redis_settings
from app.tasks.heartbeat import heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)


class WorkerSettings:
    functions = [heartbeat]
    redis_settings = redis_settings()
    queue_name = QUEUE_WORKER


if __name__ == "__main__":
    run_worker(WorkerSettings)

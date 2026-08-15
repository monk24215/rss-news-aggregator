"""Worker entrypoint.

Run with:  python -m app.worker

The worker consumes jobs from Redis and executes them (§43.1). It listens on QUEUE_WORKER.
Registered stages: the Section 1 heartbeat, source fetching (§11), and per-article
processing (§14). Later phases add clustering, scoring, and AI tasks to `functions`.
"""

import logging

from arq import run_worker

from app.queue import QUEUE_WORKER, redis_settings
from app.tasks.heartbeat import heartbeat
from app.tasks.ingest import fetch_source
from app.tasks.process import process_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)


class WorkerSettings:
    functions = [heartbeat, fetch_source, process_article]
    redis_settings = redis_settings()
    queue_name = QUEUE_WORKER


if __name__ == "__main__":
    run_worker(WorkerSettings)

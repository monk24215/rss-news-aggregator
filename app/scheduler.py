"""Scheduler entrypoint.

Run with:  python -m app.scheduler

The scheduler wakes on a timer and enqueues jobs (§43.2). It does NOT do the work itself.
In Section 1 it enqueues the heartbeat on a fixed interval; later it will enqueue
per-source RSS fetches based on each source's fetch frequency.

Design note (why this shape):
arq runs cron jobs inside a worker. To keep responsibilities clean and prevent the
scheduler's cron entries from being consumed by the *processing* worker, the two
processes use SEPARATE queues:

  - worker    -> listens on QUEUE_WORKER, runs heavy jobs (heartbeat here).
  - scheduler -> listens on QUEUE_SCHEDULER, runs only its cron, which ENQUEUES heartbeat
                 jobs onto QUEUE_WORKER for the worker to pick up.

This isolation is what makes scheduler -> worker a clean one-way hand-off.
"""

import logging

from arq import cron, run_worker

from app.config import get_settings
from app.queue import QUEUE_SCHEDULER, QUEUE_WORKER, redis_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [scheduler] %(message)s",
)
logger = logging.getLogger("scheduler")


def _cron_second_set(interval: int) -> set[int]:
    """Return the set of seconds (0-59) at which to fire, given an interval in seconds.

    For intervals that divide 60 this is exact; otherwise it approximates to the nearest
    marks within a minute, which is fine for a heartbeat.
    """
    interval = max(1, min(interval, 60))
    return {s for s in range(0, 60, interval)}


settings = get_settings()
_seconds = _cron_second_set(settings.heartbeat_interval_seconds)


async def enqueue_heartbeat(ctx: dict) -> None:
    """Cron-invoked: push a heartbeat job onto the WORKER queue."""
    job = await ctx["redis"].enqueue_job("heartbeat", _queue_name=QUEUE_WORKER)
    logger.info("enqueued heartbeat job=%s", getattr(job, "job_id", "?"))


class SchedulerSettings:
    functions: list = []  # the scheduler runs no heavy jobs itself
    cron_jobs = [cron(enqueue_heartbeat, second=_seconds, run_at_startup=True)]
    redis_settings = redis_settings()
    queue_name = QUEUE_SCHEDULER


if __name__ == "__main__":
    run_worker(SchedulerSettings)

"""Queue wiring (Redis + arq).

Redis is the job broker (§43.4). The scheduler enqueues jobs; the worker consumes them.
The web/api process must never do expensive work inline (Non-Negotiable #7) — it only
ever enqueues.

Two named queues keep the hand-off one-directional:
  - QUEUE_WORKER    : heavy jobs the worker drains (heartbeat, later ingestion/AI).
  - QUEUE_SCHEDULER : the scheduler's own cron lives here, isolated from the worker.
"""

from arq.connections import RedisSettings

from app.config import get_settings

QUEUE_WORKER = "arq:queue:worker"
QUEUE_SCHEDULER = "arq:queue:scheduler"


def redis_settings() -> RedisSettings:
    """Build arq RedisSettings from REDIS_URL."""
    return RedisSettings.from_dsn(get_settings().redis_url)

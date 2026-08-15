"""Real-services smoke test: scheduler → Redis → worker → Postgres.

Run as a module against live Postgres and Redis:

    python -m tests.smoke

This is deliberately NOT a pytest test. The unit suite stubs its dependencies so it can
run anywhere; this proves the actual wiring, which is the only thing that catches the
class of bug Section 1 hit (a shared queue letting the scheduler eat its own jobs). CI
runs it as a separate step with service containers; a human can run it locally.

It exits non-zero with a readable message on any failure.
"""

from __future__ import annotations

import asyncio
import sys

from arq import Worker, create_pool
from sqlalchemy import func, select

from app.db import SessionLocal, probe_db
from app.models import Heartbeat
from app.probes import redact_url
from app.queue import QUEUE_WORKER, redis_settings
from app.scheduler import enqueue_heartbeat
from app.tasks.heartbeat import heartbeat


def _count_heartbeats() -> int:
    with SessionLocal() as session:
        return int(session.execute(select(func.count()).select_from(Heartbeat)).scalar_one())


async def _main() -> int:
    db = probe_db()
    if not db.ok:
        print(f"FAIL database unreachable at {db.target}: {db.error}", file=sys.stderr)
        return 1
    print(f"  ok  database reachable ({db.target})")

    settings = redis_settings()
    target = redact_url(f"redis://{settings.host}:{settings.port}")

    before = _count_heartbeats()

    # 1. Act as the scheduler: enqueue one heartbeat onto the WORKER queue.
    redis = await create_pool(settings)
    await enqueue_heartbeat({"redis": redis})
    print(f"  ok  job enqueued on {QUEUE_WORKER} via {target}")

    # 2. Act as the worker: drain the queue in burst mode and stop.
    worker = Worker(
        functions=[heartbeat],
        redis_settings=settings,
        queue_name=QUEUE_WORKER,
        burst=True,
        poll_delay=0.1,
        max_jobs=10,
    )
    await worker.async_run()
    await worker.close()
    await redis.aclose()

    # 3. Prove the row landed in Postgres.
    after = _count_heartbeats()
    if after <= before:
        print(
            f"FAIL heartbeat did not reach Postgres (rows {before} -> {after})",
            file=sys.stderr,
        )
        return 1
    print(f"  ok  heartbeat written to Postgres (rows {before} -> {after})")
    print("SMOKE PASS — scheduler → Redis → worker → Postgres")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(130) from None

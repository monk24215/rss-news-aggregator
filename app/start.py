"""Unified entrypoint — dispatches on the ROLE environment variable.

Railway runs every service in this repo with the SAME start command:

    python -m app.start

Each service sets a ROLE variable to choose its behavior:

    ROLE=web        -> run migrations, then start the FastAPI server (default)
    ROLE=worker     -> run the arq worker
    ROLE=scheduler  -> run the arq scheduler

This lets one codebase power all three Railway services without needing a
per-service start-command override — the role is selected purely by an env var
(which is how §43's one-codebase/many-processes model is meant to work).
"""

import os
import sys


def _run_web() -> None:
    # Migrate to head, then serve. Import lazily so worker/scheduler roles don't
    # pay for web imports.
    from app.migrate import run as migrate_run

    migrate_run()

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )


def _run_worker() -> None:
    from arq import run_worker

    from app.worker import WorkerSettings

    run_worker(WorkerSettings)


def _run_scheduler() -> None:
    from arq import run_worker

    from app.scheduler import SchedulerSettings

    run_worker(SchedulerSettings)


ROLES = {
    "web": _run_web,
    "worker": _run_worker,
    "scheduler": _run_scheduler,
}


def main() -> None:
    role = os.environ.get("ROLE", "web").strip().lower()
    fn = ROLES.get(role)
    if fn is None:
        print(
            f"[start] unknown ROLE={role!r}; expected one of {sorted(ROLES)}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[start] role={role}")
    fn()


if __name__ == "__main__":
    main()

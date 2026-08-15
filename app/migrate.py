"""Run database migrations to head.

Invoked at container start (see Procfile / Railway start command) as:
    python -m app.migrate

This calls Alembic's Python API directly rather than the `alembic` console script or
`python -m alembic`, both of which are unreliable across environments:
  - the `alembic` shim is not always on PATH in the Railway build,
  - `python -m alembic` only works on alembic versions that ship an __main__ module.
Calling alembic.config.main(...) works on every version and needs no PATH entry.
"""

import sys

from alembic.config import main as alembic_main


def run() -> None:
    # Equivalent to: alembic upgrade head
    alembic_main(argv=["upgrade", "head"])


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # surface a clear message in deploy logs
        print(f"[migrate] migration failed: {exc}", file=sys.stderr)
        raise

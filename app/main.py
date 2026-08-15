"""FastAPI application (web + api).

This process stays thin. It serves the reader UI (server-rendered Jinja, later HTMX) and
the API, and it enqueues background jobs — but it NEVER performs expensive work inline
(Non-Negotiable #7). It exposes the health check and operational status page, and mounts the public
reader routes from `app.web`.

The health check reports *why* a dependency is down, with credentials redacted — see
`app/probes.py`. A boolean alone is not debuggable from outside the container.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import SYNC_DATABASE_URL, probe_db
from app.probes import Probe, describe_failure, redact_url
from app.web import router as public_router

try:
    # Lazy import so a missing Redis at import time never crashes the web process.
    from redis import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

settings = get_settings()
app = FastAPI(title=settings.app_name)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Shared with the public routes via app state so there is one template environment.
app.state.templates = templates
templates.env.globals["site_name"] = settings.app_name


def probe_redis() -> Probe:
    """PING Redis, reporting the reason on failure."""
    url = settings.redis_url
    target = redact_url(url)
    if Redis is None:  # pragma: no cover - redis is a hard dependency
        return Probe(False, "redis library not importable", target)
    client = None
    try:
        client = Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        return Probe(bool(client.ping()), None, target)
    except Exception as exc:
        return Probe(False, describe_failure(exc, url), target)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # pragma: no cover
                pass


# Boolean wrappers — kept because they are the simplest thing to stub in tests.
def check_db() -> bool:
    return probe_db().ok


def check_redis() -> bool:
    return probe_redis().ok


@app.get("/health")
def health() -> JSONResponse:
    db = probe_db()
    redis_probe = probe_redis()
    ok = db.ok and redis_probe.ok
    body = {
        "status": "ok" if ok else "degraded",
        "db": db.ok,
        "redis": redis_probe.ok,
    }
    if not ok:
        # Only present when something is wrong, so the healthy payload stays the
        # exact shape the Section 1 gate specified.
        body["detail"] = {"db": db.as_dict(), "redis": redis_probe.as_dict()}
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request) -> HTMLResponse:
    db = probe_db()
    redis_probe = probe_redis()
    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "app_name": settings.app_name,
            "db_ok": db.ok,
            "redis_ok": redis_probe.ok,
            "db_target": db.target or redact_url(SYNC_DATABASE_URL),
            "redis_target": redis_probe.target,
            "db_error": db.error,
            "redis_error": redis_probe.error,
        },
    )


# The reader-facing site (§34). Declared after the operational endpoints so /health and
# /status keep their own handlers.
app.include_router(public_router)


if __name__ == "__main__":
    # Allows `python -m app.main` as a PATH-independent way to start the server.
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )

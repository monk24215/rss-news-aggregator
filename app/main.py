"""FastAPI application (web + api).

This process stays thin. It serves the reader UI (server-rendered Jinja, later HTMX) and
the API, and it enqueues background jobs — but it NEVER performs expensive work inline
(Non-Negotiable #7). In Section 1 it exposes a health check and a minimal Jinja page.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import check_db

try:
    # Lazy import so a missing Redis at import time never crashes the web process.
    from redis import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

settings = get_settings()
app = FastAPI(title=settings.app_name)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def check_redis() -> bool:
    """Return True if Redis responds to PING."""
    if Redis is None:
        return False
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        return bool(client.ping())
    except Exception:
        return False


@app.get("/health")
def health() -> JSONResponse:
    db_ok = check_db()
    redis_ok = check_redis()
    status = "ok" if (db_ok and redis_ok) else "degraded"
    return JSONResponse(
        {"status": status, "db": db_ok, "redis": redis_ok},
        status_code=200 if status == "ok" else 503,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "app_name": settings.app_name,
            "db_ok": check_db(),
            "redis_ok": check_redis(),
        },
    )

# rss-news-aggregator

AI RSS News Aggregator. See the full specification in [`docs/`](docs/).

**Current state:** Section 1 — Foundation. A skeleton that boots as real services
(web + worker + scheduler on Postgres + Redis), with CI and a proven
scheduler → Redis → worker heartbeat. No product features yet.

## Architecture (target, §43)

One codebase, run as multiple processes:

| Role | Start command | Purpose |
|------|---------------|---------|
| web | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | FastAPI: serves UI + API, enqueues jobs. Never does expensive work inline. |
| worker | `python -m app.worker` | Consumes jobs from Redis, does the slow work. |
| scheduler | `python -m app.scheduler` | Wakes on a timer, enqueues jobs. Does not process. |

Postgres is the canonical store. Redis is the job broker.

## Local development

```bash
python -m pip install -e ".[dev]"
cp .env.example .env            # adjust if your local Postgres/Redis differ
alembic upgrade head            # create tables
uvicorn app.main:app --reload   # web at http://localhost:8000
# in separate terminals:
python -m app.worker
python -m app.scheduler
```

Visit `http://localhost:8000/health` — expect `{"status":"ok","db":true,"redis":true}`.
Within ~30s, `heartbeats` rows should begin accumulating (scheduler → worker proof).

## Railway deployment

The web, worker, and scheduler are three services pointing at **this same repo**, each
with the start command above. Postgres and Redis are Railway plugins; their
`DATABASE_URL` and `REDIS_URL` variables are referenced by the app services.

Set each app service's variables to reference the database services, e.g.:
```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
```

**Secrets never live in this repo** (Non-Negotiable #10). Railway injects them.

## Tests

```bash
ruff check .
pytest -q
```

CI (`.github/workflows/ci.yml`) runs lint, migrations, tests, and a real-services
smoke test of the heartbeat write path against Postgres + Redis.

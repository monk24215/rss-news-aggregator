# rss-news-aggregator

AI RSS News Aggregator. See the full specification in [`docs/`](docs/).

**Current state: v1 works end to end.** The scheduler queues due sources, the worker
fetches and normalizes them, duplicates are rejected, articles are tagged and clustered
into stories, stories are scored and placed into feeds, and the public site renders them
with full attribution — plus RSS output per feed.

Verified on live coverage from sixteen public feeds: 400 articles became 356 stories,
eleven of which gathered reporting from multiple outlets (one Israeli-settler story from
five publishers, one Lebanon airstrike from four), with 26 borderline matches routed to a
review queue rather than merged silently.

**No AI keys are required.** Headlines and summaries fall back to the publisher's own,
labelled as such. Configuring a provider upgrades them; nothing breaks without one.

See `docs/BUILD_LOG.md` for exactly where the build is.

## Architecture (target, §43)

One codebase, run as multiple processes:

| Role | Start command | Purpose |
|------|---------------|---------|
| web | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | FastAPI: serves UI + API, enqueues jobs. Never does expensive work inline. |
| worker | `python -m app.worker` | Consumes jobs from Redis, does the slow work. |
| scheduler | `python -m app.scheduler` | Wakes on a timer, enqueues jobs. Does not process. |

Postgres is the canonical store. Redis is the job broker.

## The data model

34 tables, organized by the three layers in §4.2 — original publisher data, system
analysis, presentation. `docs/SCHEMA.md` maps every table to the requirement it serves.

The rule that matters most is enforced by Postgres rather than by convention: a trigger
rejects any UPDATE that changes an `original_*` column on `articles` (Non-Negotiable #1).
Once a publisher's headline is overwritten there is nothing to restore it from, so that
rule does not rely on everyone remembering it.

```
articles.original_headline   Layer 1  immutable, trigger-protected
articles.importance_score    Layer 2  recomputed freely
ai_results.content           Layer 3  versioned, append-only, never overwrites Layer 1
```

## Trying it

```bash
alembic upgrade head
python -m scripts.seed          # source types, tags, 16 sources, 2 feeds
python -m app.worker &          # fetches and processes
python -m app.scheduler &       # queues due sources every minute
uvicorn app.main:app            # the site at http://localhost:8000
```

| Route | What it is |
|-------|-----------|
| `/` | Trending and latest (§34.2) |
| `/story/<slug>` | One story: summary, every source article, timeline, why it is here (§34.3) |
| `/topics`, `/topic/<slug>` | Browse by tag |
| `/sources` | Browse by publication, with fetch health |
| `/feed/<slug>` | One feed instance |
| `/rss/<slug>` | That feed as RSS — our headline, the publisher's link (§30) |
| `/search?q=` | Keyword search across headlines and summaries |
| `/health`, `/status` | Operational |

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
pytest -q                 # 65 tests; schema tests skip if no Postgres is reachable
python -m tests.smoke     # real scheduler → Redis → worker → Postgres proof
```

The schema tests run against real Postgres on purpose: the guarantees they check
(triggers, CHECK constraints, partial unique indexes) live in the database, and passing
them against SQLite would prove nothing about production.

CI (`.github/workflows/ci.yml`) runs lint, migrations, tests, and a real-services
smoke test of the heartbeat write path against Postgres + Redis.

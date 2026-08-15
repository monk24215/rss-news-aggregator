# Section 1 Instruction — Foundation

**Phase:** 1 (§47)
**Goal:** A repository that boots as real services on Railway, with CI green, a health
check, database migrations wired to Postgres, Redis connected, and a proven
scheduler → Redis → worker path. No product features yet — just a skeleton that stands.

**Do not exceed this scope.** No RSS, no AI, no models beyond what the heartbeat needs.

---

## Why this section exists

Everything downstream hangs on the runtime shape in §43 and Non-Negotiable #7 (nothing
expensive on the request path). Before we build ingestion or intelligence, we prove the
backbone works: the web process stays thin, and slow work runs in a worker triggered by a
scheduler. If we can enqueue a job on a timer and watch a worker run it, the spine is real.

---

## Non-Negotiables in play

- **#7** — web process must not block on background work. Heartbeat proves the split.
- **#8** — every pipeline stage independently retryable. Heartbeat job must be retry-safe.
- **#10** — production secrets never in source control. All config via env vars.

---

## Scope / Deliverables

### 1. Repository structure (monorepo, one codebase, multiple roles)
```
rss-news-aggregator/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app; health check; serves Jinja later
│   ├── config.py          # Pydantic settings from env (DATABASE_URL, REDIS_URL, ...)
│   ├── db.py              # SQLAlchemy/SQLModel engine + session
│   ├── queue.py           # Redis connection + job enqueue helper
│   ├── worker.py          # Worker entrypoint (consumes jobs)
│   ├── scheduler.py       # Scheduler entrypoint (enqueues heartbeat on a timer)
│   └── tasks/
│       ├── __init__.py
│       └── heartbeat.py   # trivial retry-safe task: write a row / log a tick
├── alembic/               # migrations
│   ├── env.py
│   └── versions/
├── alembic.ini
├── templates/
│   └── health.html        # minimal Jinja page (proves server-rendered UI works)
├── tests/
│   ├── test_health.py
│   ├── test_config.py
│   └── test_heartbeat.py
├── .github/workflows/ci.yml
├── pyproject.toml         # or requirements.txt — pin versions
├── Procfile               # or railway start commands, documented in README
├── .env.example           # names only, no values
├── .gitignore
└── README.md              # how to run each role locally + on Railway
```

### 2. Services and start commands (same repo)
- **web:**       `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **worker:**    `python -m app.worker`
- **scheduler:** `python -m app.scheduler`

Document these in README. On Railway, worker and scheduler are added as services
pointing at this same repo with these start commands.

### 3. Queue choice
Use a lightweight, well-supported Python queue on Redis. **Recommended: `arq`** (async,
minimal, pairs naturally with FastAPI's async) or **RQ** if simpler is preferred.
Whichever is chosen, the heartbeat task must be:
- enqueued by the scheduler on an interval (e.g. every 30–60s for the test),
- executed by the worker,
- **retry-safe** (running it twice does no harm) — satisfies NN#8.

### 4. Health check
- `GET /health` returns JSON `{"status":"ok","db":<bool>,"redis":<bool>}` after checking
  a real Postgres connection and a real Redis ping.
- `GET /` renders `templates/health.html` (proves Jinja serving works) — a plain page,
  no styling ambition yet.

### 5. Database + migrations
- Alembic initialized and pointed at `DATABASE_URL`.
- One migration creating a single tiny table (e.g. `heartbeats(id, created_at)`) so the
  heartbeat task has somewhere to write. This validates the migration path end to end.
- **No domain tables yet** — those are Section 2, built against the three-layer model.

### 6. Config & secrets
- All config via Pydantic `Settings` reading env vars: `DATABASE_URL`, `REDIS_URL`, plus
  any queue settings. `.env.example` lists names only.
- Nothing secret committed. `.gitignore` excludes `.env`.

### 7. CI (GitHub Actions)
- Spin up Postgres + Redis service containers.
- Run migrations.
- Run `pytest`.
- Lint (ruff) + basic build validation.
- Must be green on a PR before merge.

### 8. Build reproducibility
- Pin dependency versions.
- Ensure the Docker/Nixpacks build uses THIS repo's code — no cloning an upstream
  template at build time.

---

## The Gate (all must pass to clear Section 1)

1. **CI green** on a pull request (migrations run, `pytest` passes, lint clean).
2. **web** service Online on Railway; `GET /health` returns `db:true, redis:true`.
3. **`GET /`** renders the Jinja health page.
4. **worker** service Online on Railway.
5. **scheduler** service Online on Railway.
6. **Heartbeat proven:** within a few minutes of all services running, heartbeat rows
   accumulate in Postgres (or a clear worker log of executed ticks) — demonstrating
   scheduler → Redis → worker end to end.
7. **NN#10 check:** no secrets in the diff; config is env-only.

Clearing this gate proves the pipeline backbone before any ingestion is built on it.

---

## Explicitly deferred (NOT this section)

- Any domain models (sources, articles, stories, tags) → Section 2.
- Auth / roles → Section 3.
- Any RSS fetching, AI, scoring, feeds → later phases.
- Styling/design of the reader UI → Phase 3+.
- Retiring the template `fastapi-postgresql` service → after web boots green.

---

## On completion, report

- Link to the merged PR + CI run.
- Screenshot/text of `/health` output.
- Evidence of accumulating heartbeats (row count or worker log).
- Confirmation of the three services Online.
- Anything that had to deviate from this instruction, and why.

Claude AI will then review, update the Build Log, and issue Section 2.

# Section 1 — Deploy & Gate-Clearing Checklist

Everything here is already verified locally against real Postgres + Redis. These steps
get it green on **Railway**, which is what fully clears the Section 1 gate.

---

## Step 1 — Put the files in your repo

Unzip this into your local clone of `rss-news-aggregator` (it contains `app/`, `alembic/`,
`docs/`, CI, etc.). Then:

```bash
git checkout -b section-1-foundation
git add .
git commit -m "Section 1: foundation — FastAPI + worker + scheduler + Postgres + Redis, heartbeat proven"
git push -u origin section-1-foundation
```

Open a **pull request** into `main`.

## Step 2 — Watch CI go green

The PR triggers `.github/workflows/ci.yml`, which spins up Postgres + Redis, runs
migrations, ruff, pytest, and the heartbeat write-path smoke test. It must be green.

**Gate item 1 ✅ when CI passes.**

Merge the PR once green.

## Step 3 — Point your Railway `web` service at the code

On the `rss-news-aggregator` service in Railway (the one currently showing "Build failed"):

- **Settings → Root Directory:** leave blank (code is at repo root).
- **Variables** — reference the database services (do NOT paste raw credentials):
  ```
  DATABASE_URL=${{Postgres.DATABASE_URL}}
  REDIS_URL=${{Redis.REDIS_URL}}
  ```
  (Use the exact service names from your canvas — e.g. `Postgres-3wpg`, `Redis-Jllm`.
  In Railway: type `${{` and it autocompletes the available services/variables.)
- **Settings → Deploy → Start Command:**
  ```
  python -m app.migrate && python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```
  Why this exact form (both alternatives fail on Railway):
  - `alembic upgrade head` → `alembic: command not found` (shim not on PATH).
  - `python -m alembic upgrade head` → `No module named alembic.__main__` on the alembic
    version in the Railway image (older alembic has no __main__).
  - **`python -m app.migrate`** calls Alembic's Python API directly — works on any version,
    no PATH entry needed. Verified from a clean database: it runs `0001_create_heartbeats`.
  - `python -m uvicorn` is safe (uvicorn ships __main__); `python -m app.main` also works
    as a fallback.
- Redeploy. When it's Online, open the service URL:
  - `/health` → `{"status":"ok","db":true,"redis":true}`
  - `/` → the Jinja health page renders.

**Gate items 2 & 3 ✅.**

## Step 4 — Add the `worker` service (same repo)

Railway → **New → GitHub Repo → `rss-news-aggregator`** (same repo again), then:

- **Variables:** same `DATABASE_URL` and `REDIS_URL` references as web.
- **Start Command:** `python -m app.worker`
- Deploy. It should go Online and idle, waiting for jobs.

**Gate item 4 ✅.**

## Step 5 — Add the `scheduler` service (same repo)

Same as Step 4, one more service:

- **Variables:** same `DATABASE_URL` and `REDIS_URL`. Optionally set
  `HEARTBEAT_INTERVAL_SECONDS=30` (default is 30).
- **Start Command:** `python -m app.scheduler`
- Deploy → Online.

**Gate item 5 ✅.**

## Step 6 — Prove the loop in production

Within a minute of all three services running, heartbeat rows should accumulate.

Check the **worker** service logs — you should see:
```
[worker] heartbeat tick id=1 ...
[worker] heartbeat tick id=2 ...
```
And the **scheduler** logs:
```
[scheduler] enqueued heartbeat job=...
```

Or query Postgres (Railway Postgres → Data / Query):
```sql
SELECT count(*), max(created_at) FROM heartbeats;
```
The count should climb each minute.

**Gate item 6 ✅ — the scheduler → Redis → worker → Postgres backbone is live in production.**

## Step 7 — Report back

Send me:
- CI run result (green?),
- `/health` output from the live web URL,
- worker log snippet showing ticks (or the row count climbing),
- confirmation all three services are Online.

I'll update the Build Log, mark Section 1 **cleared**, and issue the Section 2 Instruction
(the three-layer data model: sources, articles, stories, tags — built against §4.2 / §6).

---

## Notes

- **Retire the template `fastapi-postgresql` service** once your `web` is Online and green.
  It was scaffolding; the real app now lives in your repo's service. (Leaving it running
  does no harm, but it's clutter and a small cost.)
- **Secrets:** everything is env-referenced. Nothing sensitive is in the repo
  (Non-Negotiable #10). Keep it that way.
- If `/health` shows `db:false` or `redis:false`, the variable references are the usual
  culprit — confirm the service names in `${{...}}` match your canvas exactly.

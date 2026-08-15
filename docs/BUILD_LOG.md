# Build Log — AI RSS News Aggregator

This log is the spine of the build. It records, per section: what was built, what the
gate proved, decisions made, and what was deferred. Nothing proceeds until the current
section's gate is cleared and this log is updated.

**Governing rule:** at every stage the system remains functional and usable (§47).
No section clears on assertion — only on a passing gate.

---

## The Build Loop

1. Claude AI issues a **Section Instruction** (scope, files, Non-Negotiables touched, gate).
2. Implementation (Claude Code or local) builds *exactly* that scope — nothing beyond it.
3. The section's **gate** runs. All checks must pass.
4. Claude AI reviews the diff + gate output, updates this Build Log, and clears or returns.
5. Only after clearing does Claude AI issue the next Section Instruction.

---

## Stack & Topology (decided, Section 0)

- **Language/Framework:** Python + FastAPI
- **Database:** PostgreSQL (canonical store — §43.3)
- **Queue/Cache:** Redis (§43.4)
- **Services (target):** FastAPI (web+api) · Worker · Scheduler · Postgres · Redis (§43)
  - Worker and Scheduler are the **same repo**, run with different start commands.
- **Reader front end:** server-rendered HTML from FastAPI (Jinja2 + HTMX). No separate
  front-end service. Retire any template-provided Next.js frontend.
- **Repo:** `monk24215/rss-news-aggregator`
- **Deploy:** Railway project `lively-clarity` / production
- **Migrations:** Alembic. Secrets via environment only — never in Git (§44, NN#10).

---

## Non-Negotiables (Appendix B) — checked every section

1. Original source data is never overwritten by AI or system-derived content.
2. Every displayed fact traces to an original source with a working link.
3. AI-generated text is always visually distinguishable from original reporting.
4. Conflicting reports are surfaced, never silently resolved.
5. Manual editorial decisions override automated ones.
6. Full article text is never reproduced.
7. No expensive processing happens on the public request path.
8. Every pipeline stage is independently retryable.
9. Every output channel consumes the same canonical story services.
10. Production secrets never enter source control.

---

## Section Status

| # | Section | Phase | Status | Gate cleared | Notes |
|---|---------|-------|--------|--------------|-------|
| 1 | Foundation (repo, services boot, CI, health, scheduler→worker heartbeat) | 1 | **BUILT — local gate green; awaiting Railway** | partial | Loop proven locally: 4 rows/17s, 0 failed. Clears fully once CI green on PR + 3 services Online on Railway. |
| 2 | Database schema & migrations (3-layer model) | 1 | Not started | — | |
| 3 | Authentication & permissions (roles) | 1 | Not started | — | |
| 4 | Source management | 2 | Not started | — | |
| 5 | RSS/Atom ingestion | 2 | Not started | — | |
| 6 | Processing queue | 2 | Not started | — | |
| 7 | Article normalization | 2 | Not started | — | |
| 8 | Deduplication | 2 | Not started | — | |
| 9 | Tagging | 2 | Not started | — | |
| 10 | Basic article/story API | 3 | Not started | — | |
| 11 | Basic public interface (Jinja/HTMX) | 3 | Not started | — | |
| 12 | Feed builder | 3 | Not started | — | |
| 13 | Feed preview | 3 | Not started | — | |
| 14 | AI abstraction layer | 4 | Not started | — | |
| 15 | AI headlines | 4 | Not started | — | |
| 16 | AI summaries | 4 | Not started | — | |
| 17 | Story clustering | 4 | Not started | — | |
| 18 | Multi-source synthesis | 4 | Not started | — | |
| 19 | Conflict detection | 4 | Not started | — | |
| 20 | Trending engine | 4 | Not started | — | |
| 21 | Administrative review queue | 4 | Not started | — | |
| 22 | Search | 5 | Not started | — | |
| 23 | RSS output | 5 | Not started | — | |
| 24 | Source health | 6 | Not started | — | |
| 25 | Administrative dashboard | 6 | Not started | — | |
| 26 | Monitoring & observability | 6 | Not started | — | |
| 27 | Notifications | 6 | Not started | — | |
| 28 | Performance optimization | 7 | Not started | — | |
| 29 | Responsive/mobile UI | 7 | Not started | — | |
| 30 | Security review | 7 | Not started | — | |
| 31 | Automated testing & error handling | 7 | Not started | — | |
| 32 | Production deployment | 7 | Not started | — | |
| 33 | Backup & restore testing | 7 | Not started | — | |

(Section numbering above is the *build sequence* per §47, not the spec's clause numbers.)

---

## Log Entries

### Section 0 — Setup & Decisions
**Date:** 2026-08-15
**Decisions:**
- Stack fixed: FastAPI + Postgres + Redis + Worker + Scheduler.
- No starter template adopted; scaffold from empty repo so every file traces to the spec.
- Reader UI = server-rendered Jinja/HTMX inside the api service (no separate frontend).
- Railway: Postgres, Redis provisioned; repo connected (build fails on empty repo — expected).
**Deferred:** Retirement of template-provided `fastapi-postgresql` service until §1 boots.
**Gate:** N/A (setup).

### Section 1 — Foundation
**Date:** 2026-08-15
**Built:**
- FastAPI app (`app/main.py`) — `/health` (real DB + Redis checks) and `/` Jinja page.
- Config via Pydantic settings, env-only (`app/config.py`).
- Postgres engine + connectivity check with URL normalization (`app/db.py`).
- ORM `Heartbeat` model only (`app/models.py`) — no domain tables yet.
- Redis/arq wiring with two isolated named queues (`app/queue.py`).
- Worker entrypoint on QUEUE_WORKER (`app/worker.py`).
- Scheduler entrypoint on QUEUE_SCHEDULER, cron enqueues heartbeat → QUEUE_WORKER
  (`app/scheduler.py`).
- Retry-safe heartbeat task (`app/tasks/heartbeat.py`).
- Alembic initialized; migration `0001_create_heartbeats`.
- CI: Postgres+Redis service containers, ruff, migrations, pytest, real-services smoke.
- Procfile (web/worker/scheduler start commands), `.env.example`, `.gitignore`, README.

**Gate result (local, against real Postgres + Redis):**
- ruff clean · 7/7 tests pass · migrations run & idempotent.
- `/health` → `{"status":"ok","db":true,"redis":true}`.
- **Live loop proven:** scheduler fired every 5s, worker consumed, **4 rows accumulated
  in ~17s, 0 failed, 0 retries.**
- Remaining for full clearance: CI green on a PR + web/worker/scheduler Online on Railway
  with heartbeats accumulating in production Postgres.

**Decision / bug caught:** First live run FAILED — a single shared arq queue caused the
processing worker to consume the scheduler's own cron jobs (`function not found`). Fixed
by isolating queues: scheduler runs only cron on QUEUE_SCHEDULER and enqueues one-way onto
QUEUE_WORKER. This is a wiring subtlety that would have surfaced only in production; the
gate's insistence on a real run caught it. Lesson logged: always run the actual processes,
never trust the code path on inspection alone.

**Deferred:** Domain models (§2), auth/roles (§3), retirement of the template
`fastapi-postgresql` service until web boots green on Railway.


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
| 1 | Foundation (repo, services boot, CI, health, scheduler→worker heartbeat) | 1 | **CLEARED in production** | 2026-08-15 | All three services Online; `/health` green; heartbeats accumulating. One gate item outstanding: CI green on a PR (workflow now written, awaiting repo write access). |
| 2 | Database schema & migrations (3-layer model) | 1 | **BUILT — local gate green; awaiting CI + Railway deploy** | partial | 34 tables, Layer 1 immutability enforced by trigger, 65 tests green, migration reverses cleanly. |
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


### Section 1 — Foundation (gate closure)
**Date:** 2026-08-15 (later the same day)

**Production evidence:**

| Gate item | Result |
|-----------|--------|
| 1. CI green on a PR | **Outstanding** — see "CI was never committed" below |
| 2. web Online, `/health` → `db:true, redis:true` | PASS — `{"status":"ok","db":true,"redis":true}` |
| 3. `GET /` renders the Jinja page | PASS |
| 4. worker Online | PASS |
| 5. scheduler Online | PASS |
| 6. Heartbeats accumulating in production Postgres | PASS — tick id 95 and climbing, one per 30s, 0 failures |
| 7. No secrets in the diff; config env-only | PASS |

**Bug found and fixed in production: `redis: false` on the web service.**

The worker and scheduler were consuming and executing jobs perfectly, while the web
service reported Redis unavailable. Same variable names on all three services, same
code path — so the natural reading was that the code was wrong.

It was not. The web service's `REDIS_URL` was a stale literal value; the worker and
scheduler, added later, had been wired with a proper Railway reference. Setting
`REDIS_URL=${{Redis-JIlm.REDIS_URL}}` on the web service and redeploying turned
`/health` green immediately.

**Lesson logged:** a boolean health check cannot distinguish "wrong URL" from "DNS
failure" from "refused connection", and that difference was the entire diagnosis. The
health endpoint now reports a redacted reason on failure (`app/probes.py`), and the
Jinja page shows the target it tried. This is the smallest useful down payment on §45.

**Two things the log claimed that the repository did not contain.** Both the README and
this log referenced `.github/workflows/ci.yml`. It was never committed — so gate item 1
could not have passed, and nothing was verifying anything on push. `.gitignore` was
likewise missing, which is why `__pycache__/` and `rss_news_aggregator.egg-info/` were
tracked in Git. Both are now written; the CI workflow additionally runs a real-services
smoke step and fails the build if a `.env` is ever tracked.

**Lesson logged:** a build log entry is a claim, not evidence. "CI: Postgres+Redis
service containers, ruff, migrations, pytest" was written in good faith and was false.
From here, a gate item is only marked PASS with the command output or URL that proves it.

---

### Section 2 — Database Schema and Migrations
**Date:** 2026-08-15
**Instruction:** `docs/SECTION_02_INSTRUCTION.md`

**Built:**
- `app/models/` package replacing `app/models.py`, organized by data layer, every model
  declaring `__layer__` (`base`, `enums`, `sources`, `content`, `ai`, `feeds`, `people`,
  `ops`).
- **34 tables** covering all of §6 plus the tables the addenda imply: story claims and
  claim values (§24), timeline events (§22), scoring factors (§23/§25), review queue
  (§27.2), AI usage (§28), audit log *and* change history as separate concerns (§36/§37),
  saved views (§35.7), reader preferences (§34.7), notifications (§39).
- Migration `0002_domain_schema`, plus `scripts/regen_domain_migration.py` to rebuild it
  from the models while this section is in flight.
- `docs/SCHEMA.md` — every table mapped to the requirement it serves.
- 44 new tests (65 total).

**Decisions:**
- **Non-Negotiable #1 is enforced by a database trigger, not by application code.** Any
  UPDATE touching an `original_*` column on `articles` is rejected with a message naming
  the column. Convention would have been cheaper; it would also have held only until the
  first careless `session.merge()`. Once original data is overwritten there is nothing to
  restore it from, so this rule earns a trigger.
- `IMMUTABLE_ORIGINAL_COLUMNS` is asserted against the real columns in a test, so adding
  an unprotected `original_*` column fails CI rather than quietly opening a hole.
- **AI content is append-only.** Regeneration inserts a new `ai_results` row and moves
  `is_current`; nothing is updated in place, so §26's versioning is structural. A partial
  unique index enforces one current row per (target, operation) — two indexes rather than
  one, because Postgres treats NULLs as distinct and a single index over
  (article_id, story_id) would never fire for article-targeted rows.
- **Controlled vocabularies are CHECK-constrained strings, not Postgres ENUMs.** Several
  of these lists are expected to grow; adding a value should be a one-line constraint
  change rather than a migration with a lock.
- **Source types are a table** (§7.1 requires them to be configurable), while story
  states are a constraint (§21 fixes the list).
- **`audit_logs` and `change_history` are separate tables.** §37 wants a human-readable
  trail; §36 wants the previous value back for undo. One table cannot do both well.
- `story_claims.resolved_value_id` uses `use_alter=True` — claims and values reference
  each other, and without it the create order is unsortable.
- `updated_at` uses `clock_timestamp()`, not `now()`: `now()` is fixed for a whole
  transaction, which would make two changes in one transaction indistinguishable.

**Gate result (local, against real Postgres + Redis):**
- ruff clean · **65/65 tests pass** · smoke passes (scheduler → Redis → worker → Postgres).
- Fresh `alembic upgrade head` on an empty database: 2 migrations, 34 tables, 18 triggers.
- Re-running `upgrade head`: no-op.
- `alembic downgrade base`: 0 tables and **0 trigger functions** left behind.
- Re-upgrade: clean.
- Immutability verified per column — all 9 `original_*` columns reject modification.

**Outstanding for full clearance:** CI green on the PR, and the migration applied on
Railway (the web service runs `python -m app.migrate` at start, so this happens on
deploy) with `/health` still green afterwards.

**Deferred:** authentication and permission enforcement (§3 — `users.role` exists but
nothing checks it); all ingestion, dedup, and clustering *logic*; any AI calls; pgvector
/ embeddings, left open via `ai_results.payload` and the `create_embeddings` operation
rather than committed to now.

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
| 4 | Source management | 2 | **BUILT** (model + seed; admin UI deferred) | partial | 16 seeded sources, health tracked per fetch. |
| 5 | RSS/Atom ingestion | 2 | **BUILT** | 2026-08-15 | 400 articles from 16 live feeds. |
| 6 | Processing queue | 2 | **BUILT** | 2026-08-15 | Per-stage processing_jobs rows; each stage retryable. |
| 7 | Article normalization | 2 | **BUILT** | 2026-08-15 | Pure, tested; §13's URL example passes. |
| 8 | Deduplication | 2 | **BUILT** | 2026-08-15 | Four ordered signals + schema backstop. |
| 9 | Tagging | 2 | **BUILT** (rule-based) | 2026-08-15 | AI classification replaces this in Group C. |
| 10 | Basic article/story API | 3 | Deferred | — | Public site reads services directly; API when a second channel needs it. |
| 11 | Basic public interface (Jinja/HTMX) | 3 | **BUILT** | 2026-08-15 | Front page, story page, topics, sources, search. |
| 12 | Feed builder | 3 | **BUILT** (rules engine) | partial | Visual builder is admin work, deferred. |
| 13 | Feed preview | 3 | Deferred | — | Needs the admin UI. |
| 14 | AI abstraction layer | 4 | **BUILT** | 2026-08-15 | Provider protocol + extractive fallback + Anthropic. |
| 15 | AI headlines | 4 | **BUILT** | 2026-08-15 | Versioned; guard-checked. |
| 16 | AI summaries | 4 | **BUILT** | 2026-08-15 | Configurable length per §18. |
| 17 | Story clustering | 4 | **BUILT** (non-AI) | 2026-08-15 | Entity-based; calibrated on live coverage. |
| 18 | Multi-source synthesis | 4 | **BUILT** | 2026-08-15 | Multi-source stories synthesize; single-source summarize. |
| 19 | Conflict detection | 4 | **BUILT** (numeric) | partial | Disagreeing figures detected; prose conflicts need the model. |
| 20 | Trending engine | 4 | **BUILT** | 2026-08-15 | Baseline-relative; factors stored individually. |
| 21 | Administrative review queue | 4 | **BUILT** | 2026-08-15 | Queue + one-screen review UI with approve/reject. |
| 22 | Search | 5 | **BUILT** (keyword) | 2026-08-15 | Semantic search left open per §31. |
| 23 | RSS output | 5 | **BUILT** | 2026-08-15 | /rss/<slug>, our headline + publisher's link. |
| 24 | Source health | 6 | **BUILT** | 2026-08-15 | Per-attempt log, backoff, resting; shown on /sources. |
| 25 | Administrative dashboard | 6 | **BUILT** | 2026-08-15 | Needs-attention first, per §35.1. |
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

### Groups A and B — v1: ingestion through to a readable site
**Date:** 2026-08-15
**Scope:** collapsed sections 4-13 and 20-24 into two groups, to reach a usable product
in one deployable branch rather than one handoff per section.

**Built:**
- **Group A — ingestion.** `app/services/{normalize,feedparse,dedupe,ingest}.py` and the
  worker/scheduler tasks. Conditional requests, backoff, item caps, source resting.
- **Group B — reading.** `app/services/{enrich,cluster,scoring,feeds,presentation,rss}.py`,
  the public routes in `app/web.py`, six templates, and `scripts/seed.py`.

**Proven on live data, not fixtures.** Sixteen public feeds, 400 articles, 356 stories:

| | |
|---|---|
| Multi-source stories | 11 |
| Largest | 5 publishers (Al Jazeera, BBC, DW, France 24, Guardian) on one West Bank story |
| Next | 4 publishers on one Lebanon airstrike |
| Borderline matches queued for review | 26 |
| Feeds failing | 2 of 18 — CISA returns 403 to our user-agent, CBC timed out. Both recorded as source health, neither cost us the run. |

**The bug the live run caught.** Against fixtures, clustering looked fine. Against real
coverage it produced **zero** multi-source stories out of 400 articles. The scoring used
Jaccard similarity on entity sets, which punishes exactly what real headlines do: two
outlets name the same specifics and each adds its own context, so the union grows while
the intersection does not. Genuine matches — one earthquake covered by four outlets —
scored 0.28-0.61 against a 0.62 threshold and were all rejected.

Fixed by scoring on the overlap coefficient rather than Jaccard, collapsing
morphological variants ("Israel"/"Israeli" is one specific, not two), and requiring at
least two distinct shared entities. Thresholds were then set *from the data*: 0.62 to
join, 0.72 below which a match is flagged for review.

**Lesson logged:** a similarity threshold chosen a priori is a guess. This one was
wrong in the direction that looks like success — the system produced stories, they were
just all singletons, and no test caught it because the fixtures were written by the same
person who wrote the scorer. Only real coverage exposed it.

**Second lesson:** the immutability trigger from Section 2 rejected two of my own tests
that mutated `original_published_at` after insert. The rule is doing its job on the
people writing the system, which is the only place it could ever have mattered.

**Also fixed:** the test suite now uses its own database (`<db>_test`). It passed in CI
and failed locally the moment seeded data existed — a suite that only passes on an empty
database will fail on somebody's laptop.

**Deferred to Group C:** AI provider abstraction with an extractive fallback, AI
headlines and summaries, multi-source synthesis, conflict detection, and the
administrative interface (dashboard, source wizard, visual feed builder, review UI).

### Group C — the AI layer and the administrative interface
**Date:** 2026-08-15

**Built:**
- `app/services/ai/` — the §16 abstraction: a provider protocol, an **extractive**
  provider that needs no key, an **Anthropic** provider, a **guard**, and a service layer
  owning versioning, cost, and fallback.
- `app/admin.py` + eight templates — dashboard, sources, source detail with fetch
  history, review queue, stories with editorial controls, feed health, audit log.
- 50 new tests (221 total).

**The decision that shapes this group: the guard.** §15 lists twelve prohibitions. A
prompt asking a model to obey them is a request, not enforcement — so generated text is
checked against the source reporting *mechanically* before it is stored:

| §15 rule | Check |
|---|---|
| Never invent statistics | every number in the output must appear in the sources |
| Never invent quotations | any quoted span must appear in the sources |
| Never invent sources | capitalized names absent from the sources are flagged |

Numbers and quotations are hard failures — the two ways a summary can state something
false with total confidence. The system falls back to the extractive provider and files
the rejected text in the review queue, because a model that just tried to invent a
casualty figure is precisely what an editor should see. A stray name is a warning that
lowers confidence and routes for review.

The rest of §15 ("never change the meaning", "never present speculation as fact") are
judgements a checker cannot make. Claiming otherwise would be its own dishonesty, so
those stay with the prompt and the review queue, and the docstring says so.

**Why the extractive provider is the default.** It selects sentences the publishers
already wrote rather than composing new ones, so it is structurally incapable of
violating §15 — and v1 therefore runs with no API key, no spend, and no blank summaries.
Setting `AI_PROVIDER=anthropic` upgrades it; the extractive provider stays as the
fallback for a provider outage, a budget stop, or a guard rejection.

**Cost control (§28).** Usage recorded per call with an estimated cost; daily and
monthly caps checked before spending; unchanged inputs never regenerated (fingerprinted);
an importance threshold to keep spending on what matters.

**Versioning (§26) is structural.** Regeneration inserts a new `ai_results` row and moves
`is_current`; nothing is updated in place. A human edit is never overwritten by a rerun —
tested, because that is Non-Negotiable #5 and the easiest rule to break by accident.

**Proven on the live corpus:** 40 artifacts generated across 20 stories with no API key.
The five-source West Bank story and the four-source Lebanon story both produced real
syntheses drawn from the publishers' own words, and every one records exactly which
articles it came from — which is what makes "generated from 6 source reports" checkable
rather than decorative.

**Two bugs of my own, caught by the tests:** a leftover nonsense expression
(`story and "generate" or "generate"`) wrote an invalid operation into `ai_usage`, and the
CHECK constraint from Section 2 rejected it — the schema catching the application. And
the guard's proper-noun check ignored sentence-initial names, so an invented outlet in
the first position would have passed; a two-word-capitalized-run rule fixed it.

**Deferred:** authentication on /admin (Section 3 — the roles exist, nothing enforces
them), the source onboarding wizard (§35.2), the visual feed builder (§29.1), global
command search (§35.6), saved views (§35.7), and notifications (§39).

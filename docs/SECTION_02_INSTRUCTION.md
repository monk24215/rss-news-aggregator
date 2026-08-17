# Section 2 Instruction — Database Schema and Migrations

**Phase:** 1 (§47)
**Goal:** The canonical data model exists in Postgres, built around the three layers in
§4.2, with the layer separation enforced by the database rather than by convention.

**Do not exceed this scope.** No ingestion, no AI calls, no admin screens, no API. This
section produces tables, constraints, migrations, and the tests that prove them.

---

## Why this section exists

Every later section writes into this schema. Two of the ten Non-Negotiables are
structural claims about it:

- **#1** Original source data is never overwritten by AI or system-derived content.
- **#6** Full article text is never reproduced.

Those cannot be retrofitted. If Layer 1 is mutable for even one release, the first
overwrite is unrecoverable — there is nothing to restore the publisher's headline from.
So the schema is where those rules get teeth, before a single article is fetched.

The other reason to do this now: §4.1 requires one canonical model that every output
channel consumes. Deciding the shape once, here, is what stops the RSS generator, the
admin UI, and the future email digest from each growing their own version of a story.

---

## Non-Negotiables in play

- **#1** — `original_*` columns must be immutable at the database level.
- **#6** — no column may hold article body text.
- **#5** — manual editorial decisions must be representable and must be able to
  outrank automated ones (flags, not overwrites).
- **#8** — pipeline stages are independently retryable, so jobs are per stage.
- **#9** — one canonical model; no output-specific copies of articles or stories.

---

## Scope / Deliverables

### 1. Model package

Replace `app/models.py` with a package organized by layer:

```
app/models/
├── __init__.py    re-exports everything; declares IMMUTABLE_ORIGINAL_COLUMNS
├── base.py        Base, TimestampMixin, naming convention, layer constants
├── enums.py       controlled vocabularies as CHECK-constrained strings
├── sources.py     source_types, sources, tags, source_tags, source_fetch_logs
├── content.py     articles, stories, story_articles, article_tags, story_tags,
│                  article_entities, story_timeline_events, story_claims,
│                  story_claim_values, scoring_factors
├── ai.py          ai_results, ai_result_inputs, review_queue_items, ai_usage
├── feeds.py       feed_instances + tag/source/source_type rules + entries
├── people.py      users, saved_views, reader_preferences, settings
└── ops.py         heartbeats, processing_jobs, system_logs, audit_logs,
                   change_history, notifications
```

Every model declares `__layer__`. Import sites use `from app.models import X`.

### 2. Required tables

All of §6, plus the tables the addenda imply: `story_claims` / `story_claim_values`
(§24), `story_timeline_events` (§22), `scoring_factors` (§23, §25), `review_queue_items`
(§27.2), `ai_usage` (§28), `audit_logs` **and** `change_history` as separate things
(§36, §37), `saved_views` (§35.7), `reader_preferences` (§34.7), `notifications` (§39).

### 3. Layer 1 immutability — enforced by Postgres

A `BEFORE UPDATE` trigger on `articles` that raises if any `original_*` column changes.
The error message must name the column and cite Non-Negotiable #1, so the failure is
self-explaining in a log.

`IMMUTABLE_ORIGINAL_COLUMNS` in `app/models/__init__.py` lists the protected columns, and
a test asserts it matches the actual `original_*` columns — so adding a new original
column without protecting it fails CI instead of silently opening a hole.

### 4. AI content versioning (§26)

`ai_results` stores content, provider, model, prompt version, generation timestamp,
input fingerprint, version, `is_current`, human edit, and review state. Regeneration
inserts a new row; it never updates in place. A partial unique index guarantees at most
one current row per (target, operation).

`ai_result_inputs` records exactly which articles fed each generated artifact (§15) —
this is what makes "generated from 6 source reports" (§34.5) verifiable.

### 5. Constraints that encode spec rules

Where the spec states a rule, prefer a constraint over a comment:

| Rule | Constraint |
|------|-----------|
| §13 one item per source per GUID / normalized URL | unique constraints |
| §12 politeness | `fetch_interval_seconds >= 60` |
| §7.2 no truth score | `context` restricted to the descriptive vocabulary |
| §21 lifecycle | `state` restricted to the six states |
| §22 sourced timeline | entry has a source article or is marked manual |
| §27.1 confidence is a probability | `0 <= confidence <= 1` |
| §29 coherent feed rules | an excluded tag cannot also be required |

### 6. Migration

One migration, `0002_domain_schema`, that:

- creates every table, index, and constraint,
- installs the immutability trigger and the `updated_at` trigger,
- **downgrades cleanly** — dropping triggers and functions, not just tables.

`scripts/regen_domain_migration.py` regenerates it from the models while Section 2 is
still in flight. Once 0002 ships to an environment that cannot be rebuilt, further
changes are NEW migrations.

### 7. Tests

Against real Postgres (skipped automatically when no database is reachable):

- every `original_*` column rejects modification,
- the protected-column list matches reality,
- analysis columns on the same row remain writable,
- `updated_at` moves on change,
- AI headline coexists with the original; a human edit outranks generated text,
- only one current AI version per target and operation; history may accumulate,
- dedup, tag hierarchy, lifecycle, timeline sourcing, claim conflicts, feed rules,
- an end-to-end walkthrough of Appendix A: four reports, one story, one feed.

---

## The Gate (all must pass to clear Section 2)

1. `ruff check .` clean.
2. `alembic upgrade head` from an empty database succeeds; running it again is a no-op.
3. `alembic downgrade base` leaves no tables, no trigger functions.
4. Full test suite green, including the real-Postgres schema tests.
5. `python -m tests.smoke` still passes — Section 1's backbone is not broken.
6. CI green on the pull request.
7. Migration applied on Railway (the web service runs `python -m app.migrate` at start);
   `/health` still returns `db:true, redis:true` afterwards.
8. No secrets in the diff.

---

## Explicitly deferred (NOT this section)

- Authentication, password/session handling, permission checks → Section 3.
  `users.role` exists; nothing enforces it yet.
- Any ingestion, normalization, or dedup **logic** → Sections 5–8. This section provides
  the columns those algorithms will write to.
- Any AI calls → Section 14+. `ai_results` is an empty table for now.
- Embeddings / pgvector. §31 says design so semantic search can be added later; the
  `create_embeddings` operation and `ai_results.payload` leave room without committing
  to an extension yet.
- Seed data and fixtures beyond what tests create.

---

## On completion, report

- The migration diff and the table count.
- Output of the fresh-upgrade / idempotent / downgrade sequence.
- Test summary, including the immutability tests.
- Confirmation that `/health` is still green after the Railway deploy.

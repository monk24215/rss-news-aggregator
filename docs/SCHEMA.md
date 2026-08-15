# Data Model Reference

Every table, which of the three layers (§4.2) it belongs to, and the spec section it
serves. If a table is not traceable to a requirement, it should not exist.

## Layer 1 — Original data (immutable)

What the publisher said. Written once at ingestion. Protected by the
`articles_original_data_is_immutable` trigger, not by convention.

| Table | Purpose | Spec |
|-------|---------|------|
| `sources` | Publications we fetch from, plus fetch policy and health | §7, §12, §35.3 |
| `articles` | One item from one source; `original_*` columns are Layer 1 | §9, §13 |

`articles` has no body column, by design — Non-Negotiable #6. `original_description`
holds only the feed's own summary field.

## Layer 2 — System analysis

What the system concluded. Recomputable; never mistaken for reporting.

| Table | Purpose | Spec |
|-------|---------|------|
| `source_types` | Configurable source taxonomy | §7.1 |
| `tags` | Hierarchical, reusable across every object | §8 |
| `source_tags` | sources ↔ tags | §6 |
| `article_tags` | articles ↔ tags, with confidence | §8, §27.1 |
| `story_tags` | stories ↔ tags | §10 |
| `stories` | The event; lifecycle, counts, scores, editorial flags | §10, §21 |
| `story_articles` | Membership and how it was decided | §6, §19 |
| `article_entities` | Extracted people/orgs/places/events | §19 |
| `story_timeline_events` | Dated developments, each citing its source | §22 |
| `story_claims` | A contested fact within a story | §24 |
| `story_claim_values` | Each source's version of that fact | §24 |
| `scoring_factors` | The inputs behind a score, stored individually | §23, §25, §35.5 |
| `review_queue_items` | Decisions routed to a human | §27.2 |
| `ai_usage` | Per-call tokens, latency, and cost attribution | §28 |
| `source_fetch_logs` | One row per fetch attempt | §35.3, §45 |

## Layer 3 — Presentation

What we show. Always distinguishable from reporting.

| Table | Purpose | Spec |
|-------|---------|------|
| `ai_results` | Versioned generated content with full provenance | §17, §18, §20, §26 |
| `ai_result_inputs` | Exactly which articles fed each artifact | §15, §34.5 |
| `feed_instances` | A saved query plus a presentation policy | §29, §29.2 |
| `feed_instance_tags` | Tag rules: include / exclude / required | §29 |
| `feed_instance_sources` | Source rules | §29 |
| `feed_instance_source_types` | Source-type rules | §29 |
| `feed_instance_entries` | Materialized membership, with match reason | §29.3, §35.5 |

## Operations

| Table | Purpose | Spec |
|-------|---------|------|
| `users` | Administrative identity and role | §38 |
| `saved_views` | Stored admin filters | §35.7 |
| `reader_preferences` | Reader personalization, never on canonical rows | §34.7 |
| `settings` | Runtime policy (never secrets) | §6 |
| `processing_jobs` | One row per pipeline stage per item | §14 |
| `system_logs` | Technical events | §6, §45 |
| `audit_logs` | Who changed what, in human terms | §37 |
| `change_history` | Previous → new values, for undo | §36 |
| `notifications` | Queued operational alerts | §39 |
| `heartbeats` | Section 1's liveness proof | — |

## Three tables that are easy to conflate

`system_logs`, `audit_logs`, and `change_history` look redundant and are not:

- **system_logs** — for engineers. "Redis connection refused."
- **audit_logs** — for accountability. "John changed Power Grid News — added tag:
  Infrastructure." §37 requires this to be separate from technical logs.
- **change_history** — for undo. The previous value, machine-readable. §36 needs the old
  value back, which a log line cannot reliably provide.

An editorial change typically writes to the latter two, linked by `audit_logs.change_id`.

## Database-level guarantees

Installed by migration `0002_domain_schema`:

| Trigger | Table(s) | What it enforces |
|---------|----------|------------------|
| `articles_original_data_is_immutable` | `articles` | Non-Negotiable #1 — any UPDATE changing an `original_*` column is rejected with a message naming the column |
| `<table>_set_updated_at` | all 17 timestamped tables | `updated_at` moves on every change, using `clock_timestamp()` so two changes in one transaction are distinguishable |

## Conventions

- Constraint names follow a fixed naming convention (`app/models/base.py`), so migrations
  stay reviewable and autogenerate stays stable.
- Controlled vocabularies are CHECK-constrained strings rather than Postgres ENUMs —
  adding a value should be a one-line constraint change, not a lock.
- Confidence is always a probability in `[0, 1]`; scores are always `[0, 100]`.
- Every domain table carries `created_at` and `updated_at`.

## Changing the schema

While Section 2 is in flight, `python scripts/regen_domain_migration.py` rebuilds
`0002_domain_schema` from the models. **Once 0002 has been applied to staging or
production, stop doing that** and write a new migration instead — editing applied history
is how environments drift apart.

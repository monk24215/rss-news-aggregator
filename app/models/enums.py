"""Controlled vocabularies.

These are stored as short strings with CHECK constraints rather than Postgres ENUM
types. Adding a value to a Postgres ENUM is a migration with a lock; adding one here is
a one-line constraint change. The spec expects several of these lists to grow (story
lifecycle states, job stages, entity kinds), so the cheaper-to-evolve form wins.

Source *types* are deliberately NOT here: §7.1 requires them to be configurable by an
administrator, so they live in the `source_types` table.
"""

from __future__ import annotations

# --- Stories -----------------------------------------------------------------

#: §21 Story lifecycle. `locked` and `verified` are editorial flags on the story row
#: rather than states, because a story can be (say) Active *and* verified at once.
STORY_STATES = (
    "emerging",
    "developing",
    "active",
    "stable",
    "cooling",
    "archived",
)

# --- Sources -----------------------------------------------------------------

#: §7.2 Source context. Deliberately descriptive, never a "truth score".
SOURCE_CONTEXTS = (
    "established_publication",
    "government",
    "local",
    "industry",
    "research",
    "primary",
    "secondary",
    "opinion",
    "analysis",
)

# --- Processing --------------------------------------------------------------

#: §14 pipeline stages. Each is independently retryable (Non-Negotiable #8).
JOB_STAGES = (
    "fetch",
    "normalize",
    "deduplicate",
    "extract_metadata",
    "tag",
    "cluster",
    "generate_ai",
    "score",
    "publish",
)

JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")

# --- AI ----------------------------------------------------------------------

#: §16 operations the AI service layer exposes. Stored on ai_results so every generated
#: artifact says which operation produced it.
AI_OPERATIONS = (
    "generate_headline",
    "generate_summary",
    "classify_article",
    "extract_entities",
    "create_embeddings",
    "cluster_stories",
    "synthesize_story",
    "identify_conflicts",
)

#: What an ai_results row is attached to.
AI_TARGETS = ("article", "story")

#: §27.1 how a decision was resolved.
REVIEW_STATES = ("auto_accepted", "pending_review", "approved", "rejected", "superseded")

# --- Entities ----------------------------------------------------------------

#: §19 clustering signals that are entity-shaped.
ENTITY_KINDS = ("person", "organization", "location", "event", "date", "other")

# --- People ------------------------------------------------------------------

#: §38 roles, ordered from most to least privileged.
USER_ROLES = ("owner", "administrator", "editor", "reviewer", "read_only")

# --- Feeds -------------------------------------------------------------------

#: Join rows say whether a tag/source is being included or excluded (§29).
RULE_MODES = ("include", "exclude")

#: §29 sort orders a feed can present.
FEED_SORT_ORDERS = ("newest", "trending", "importance", "most_sources")

#: §18 configurable summary lengths.
SUMMARY_LENGTHS = ("short", "standard", "detailed")


def check_in(column: str, values: tuple[str, ...]) -> str:
    """Render a CHECK expression restricting `column` to `values`."""
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({rendered})"

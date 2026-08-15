"""The three-layer guarantees (§4.2) — tested against real Postgres.

These are the tests that matter most in this section. Every other rule in the spec can
be re-litigated later; Non-Negotiable #1 cannot, because once original data has been
overwritten there is nothing to restore it from. So we test the database's own refusal,
not the application's good intentions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import (
    IMMUTABLE_ORIGINAL_COLUMNS,
    LAYERS,
    AIResult,
    Article,
    Base,
    Source,
    Story,
)

pytestmark = pytest.mark.usefixtures("schema")


def _source(db, slug: str = "reuters") -> Source:
    src = Source(name="Reuters", slug=slug, feed_url="https://example.com/rss")
    db.add(src)
    db.flush()
    return src


def _article(db, source: Source, **kw) -> Article:
    defaults = dict(
        source_id=source.id,
        original_url="https://example.com/story?id=123",
        original_headline="Power outage hits 50,000 customers",
        original_guid="guid-123",
        original_published_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    art = Article(**defaults)
    db.add(art)
    db.flush()
    return art


# --- Layer 1: original data is immutable -------------------------------------


@pytest.mark.parametrize("column", IMMUTABLE_ORIGINAL_COLUMNS["articles"])
def test_every_original_column_is_protected(db, column):
    """Each `original_*` column rejects modification at the database level."""
    art = _article(db, _source(db, f"src-{column}"))
    # A literal per column type, chosen so the new value genuinely differs from the old
    # one — an UPDATE that changes nothing is not a test of anything.
    literal = {
        "original_published_at": "'2020-01-01T00:00:00+00'::timestamptz",
        "original_payload": "'{\"tampered\": true}'::jsonb",
    }.get(column, "'tampered'")

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text(f"UPDATE articles SET {column} = {literal} WHERE id = :id"), {"id": art.id})
    assert "Non-Negotiable #1" in str(excinfo.value)
    db.rollback()


def test_protection_list_matches_the_actual_columns(db):
    """A new `original_*` column must be added to the trigger, or this fails.

    Without this test, someone adds `original_section` next year, the trigger silently
    does not cover it, and Layer 1 has a hole nobody notices until it matters.
    """
    actual = {c.name for c in Base.metadata.tables["articles"].c if c.name.startswith("original_")}
    assert actual == set(IMMUTABLE_ORIGINAL_COLUMNS["articles"])


def test_trigger_exists_and_is_attached(db):
    """The protection is a real trigger, not just a convention in a docstring."""
    found = db.execute(
        text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgname = 'articles_original_data_is_immutable'"
        )
    ).fetchone()
    assert found is not None


def test_analysis_columns_on_the_same_row_remain_writable(db):
    """Layer 2 must stay editable — the trigger protects Layer 1 only."""
    art = _article(db, _source(db, "src-analysis"))
    art.importance_score = 82.0
    art.normalized_url = "example.com/story?id=123"
    art.is_duplicate = False
    db.flush()
    db.refresh(art)
    assert art.importance_score == 82.0
    assert art.original_headline == "Power outage hits 50,000 customers"


def test_updated_at_moves_on_change(db):
    """A row cannot be modified without the clock moving (§36, §37)."""
    art = _article(db, _source(db, "src-clock"))
    before = db.execute(
        text("SELECT updated_at FROM articles WHERE id = :id"), {"id": art.id}
    ).scalar_one()
    db.execute(
        text("UPDATE articles SET importance_score = 91 WHERE id = :id"), {"id": art.id}
    )
    after = db.execute(
        text("SELECT updated_at FROM articles WHERE id = :id"), {"id": art.id}
    ).scalar_one()
    assert after > before


# --- Layer 3: AI content never replaces the original -------------------------


def test_ai_headline_lives_beside_the_original_not_on_top_of_it(db):
    """The whole point of §4.2: both headlines exist at once (§17)."""
    art = _article(db, _source(db, "src-ai"))
    result = AIResult(
        target_type="article",
        article_id=art.id,
        operation="generate_headline",
        content="Regional outage leaves 50,000 without power",
        provider="anthropic",
        model="claude-x",
        generated_at=datetime.now(timezone.utc),
    )
    db.add(result)
    db.flush()

    assert art.original_headline == "Power outage hits 50,000 customers"
    assert result.content != art.original_headline
    assert result.display_content == result.content


def test_human_edit_wins_over_generated_text(db):
    """§35.4: manual changes override automated ones."""
    art = _article(db, _source(db, "src-edit"))
    result = AIResult(
        target_type="article",
        article_id=art.id,
        operation="generate_summary",
        content="Machine summary.",
        edited_content="Editor's summary.",
        provider="anthropic",
        model="claude-x",
        generated_at=datetime.now(timezone.utc),
    )
    db.add(result)
    db.flush()
    assert result.display_content == "Editor's summary."
    # …and the generated text is still on record.
    assert result.content == "Machine summary."


def test_only_one_current_version_per_target_and_operation(db):
    """§26 versioning: history accumulates, but the live pointer cannot fork."""
    art = _article(db, _source(db, "src-version"))
    common = dict(
        target_type="article",
        article_id=art.id,
        operation="generate_headline",
        provider="anthropic",
        model="claude-x",
        generated_at=datetime.now(timezone.utc),
    )
    db.add(AIResult(**common, content="v1", version=1, is_current=True))
    db.flush()

    db.add(AIResult(**common, content="v2", version=2, is_current=True))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_superseded_versions_may_coexist(db):
    """Two historical rows are fine — only `is_current` is constrained."""
    art = _article(db, _source(db, "src-history"))
    common = dict(
        target_type="article",
        article_id=art.id,
        operation="generate_headline",
        provider="anthropic",
        model="claude-x",
        generated_at=datetime.now(timezone.utc),
    )
    db.add(AIResult(**common, content="v1", version=1, is_current=False))
    db.add(AIResult(**common, content="v2", version=2, is_current=False))
    db.add(AIResult(**common, content="v3", version=3, is_current=True))
    db.flush()  # no error


def test_ai_result_must_point_at_exactly_one_target(db):
    """An artifact belongs to an article or a story, never both, never neither."""
    art = _article(db, _source(db, "src-target"))
    story = Story()
    db.add(story)
    db.flush()

    db.add(
        AIResult(
            target_type="article",
            article_id=art.id,
            story_id=story.id,  # contradicts target_type
            operation="generate_headline",
            provider="p",
            model="m",
            generated_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- Structural invariants ---------------------------------------------------


def test_every_model_declares_a_known_layer():
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        assert cls.__layer__ in LAYERS, f"{cls.__name__} has no valid __layer__"


def test_no_column_stores_full_article_text():
    """Non-Negotiable #6 — we store metadata and summaries, never the body."""
    banned = {"body", "content_text", "full_text", "article_body", "html"}
    columns = {c.name for c in Base.metadata.tables["articles"].c}
    assert not (columns & banned)


def test_expected_tables_exist(db):
    """The migration builds every table the models declare."""
    present = set(inspect(db.get_bind()).get_table_names())
    missing = set(Base.metadata.tables) - present
    assert not missing, f"migration is missing tables: {sorted(missing)}"

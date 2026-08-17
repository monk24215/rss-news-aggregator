"""The AI layer — §15, §16, §17, §18, §20, §26, §27, §28.

The centre of gravity here is the guard. §15 lists twelve prohibitions, and the only
honest way to hold a model to the checkable ones is to check. So the tests that matter
are the ones where a provider *tries* to invent a casualty figure and the system refuses
to publish it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models import AIResult, AIUsage, Article, ReviewQueueItem, Source, Story
from app.services.ai import guard
from app.services.ai.anthropic import AnthropicProvider
from app.services.ai.base import (
    OPERATION_CONFLICTS,
    OPERATION_HEADLINE,
    OPERATION_SUMMARY,
    OPERATION_SYNTHESIS,
    Generation,
    GenerationRequest,
    SourceText,
)
from app.services.ai.extractive import ExtractiveProvider
from app.services.ai.service import (
    REVIEW_BELOW,
    build_provider,
    enrich_story,
    generate_for_story,
    sources_for_story,
    within_budget,
)
from app.services.cluster import assign_story
from app.services.scoring import rescore

pytestmark = pytest.mark.usefixtures("schema")

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

CORPUS = (
    "Dominion Energy outage hits Chesterfield County. "
    "About 50,000 customers lost power early Tuesday after equipment failure."
)


# --- The guard: §15 made enforceable ----------------------------------------


class TestGuard:
    def test_accepts_text_drawn_from_the_sources(self):
        result = guard.check("About 50,000 customers lost power in Chesterfield County.", CORPUS)
        assert result.ok and not result.warnings

    def test_rejects_an_invented_statistic(self):
        """§15: never invent statistics. The single most damaging failure mode."""
        result = guard.check("About 65,000 customers lost power.", CORPUS)
        assert result.ok is False
        assert "65000" in result.summary.replace(",", "")

    def test_rejects_an_invented_quotation(self):
        """§15: never invent quotations."""
        result = guard.check(
            'A spokesman said "we expect power restored by midnight tonight".', CORPUS
        )
        assert result.ok is False
        assert "quotation" in result.summary

    def test_allows_a_quotation_that_is_in_the_sources(self):
        corpus = 'The utility said "crews are working around the clock" on Tuesday.'
        result = guard.check('The utility said "crews are working around the clock".', corpus)
        assert result.ok

    def test_flags_a_name_that_appears_nowhere(self):
        """§15: never invent sources. A warning, not a hard failure."""
        result = guard.check("Duke Energy restored power to the county.", CORPUS)
        assert "Duke Energy" in result.summary
        assert result.ok is True  # soft: routed for review rather than discarded

    def test_a_number_written_as_a_word_is_not_invention(self):
        result = guard.check("Three children were among them.", "three children were among them")
        assert result.ok

    def test_empty_output_fails(self):
        assert guard.check("   ", CORPUS).ok is False

    def test_confidence_falls_with_severity(self):
        clean = guard.check("Chesterfield County lost power.", CORPUS)
        dirty = guard.check("About 65,000 customers lost power.", CORPUS)
        assert guard.adjust_confidence(0.8, clean) > guard.adjust_confidence(0.8, dirty)


# --- The extractive provider: v1 with no key --------------------------------


def _request(operation=OPERATION_SUMMARY, length="standard") -> GenerationRequest:
    return GenerationRequest(
        operation=operation,
        length=length,
        sources=[
            SourceText(1, "Reuters", "Dominion Energy outage hits Chesterfield County",
                       "About 50,000 customers lost power early Tuesday after equipment failure."),
            SourceText(2, "AP", "Power restored to thousands in Chesterfield",
                       "Crews restored service to most customers by Tuesday evening."),
            SourceText(3, "Local Paper", "Chesterfield outage: what we know",
                       "The county said about 50,000 homes were affected by the outage."),
        ],
    )


class TestExtractiveProvider:
    def test_summary_uses_only_the_publishers_words(self):
        request = _request()
        generation = ExtractiveProvider().generate(request)
        assert generation.ok
        # Structurally incapable of violating §15 — every sentence came from a source.
        assert guard.check(generation.text, request.corpus).ok

    def test_headline_is_selected_not_composed(self):
        request = _request(OPERATION_HEADLINE)
        generation = ExtractiveProvider().generate(request)
        assert generation.text in [s.headline for s in request.sources]
        assert generation.payload["method"] == "selected"

    def test_length_setting_changes_the_output(self):
        short = ExtractiveProvider().generate(_request(length="short"))
        detailed = ExtractiveProvider().generate(_request(length="detailed"))
        assert len(detailed.text) >= len(short.text)

    def test_summary_does_not_repeat_the_same_fact_three_times(self):
        """Several outlets say the same thing; a summary should say it once."""
        generation = ExtractiveProvider().generate(_request())
        assert generation.text.count("50,000") <= 1

    def test_conflict_detection_finds_disagreeing_figures(self):
        """§24 — numeric disagreement is the one conflict detectable without judgement."""
        request = GenerationRequest(
            operation=OPERATION_CONFLICTS,
            sources=[
                SourceText(1, "Reuters", "Outage hits region", "About 50,000 customers affected."),
                SourceText(2, "Local Paper", "Outage hits region",
                           "About 65,000 customers affected."),
            ],
        )
        generation = ExtractiveProvider().generate(request)
        assert generation.payload["conflicts"]
        assert "50,000" in generation.text and "65,000" in generation.text

    def test_reports_no_conflict_when_sources_agree(self):
        generation = ExtractiveProvider().generate(
            GenerationRequest(
                operation=OPERATION_CONFLICTS,
                sources=[
                    SourceText(1, "A", "Outage", "About 50,000 affected."),
                    SourceText(2, "B", "Outage", "About 50,000 affected."),
                ],
            )
        )
        assert generation.payload["conflicts"] == []

    def test_never_raises_on_junk_input(self):
        generation = ExtractiveProvider().generate(
            GenerationRequest(operation=OPERATION_SUMMARY, sources=[SourceText(1, "X", "", None)])
        )
        assert generation.error is None or not generation.ok


# --- Provider selection (§16) -----------------------------------------------


class TestProviderSelection:
    def test_default_is_extractive(self, monkeypatch):
        assert isinstance(build_provider(), ExtractiveProvider)

    def test_anthropic_without_a_key_falls_back(self):
        from app.config import Settings

        settings = Settings(ai_provider="anthropic", anthropic_api_key="")
        assert isinstance(build_provider(settings), ExtractiveProvider)

    def test_anthropic_with_a_key_is_selected(self):
        from app.config import Settings

        provider = build_provider(Settings(ai_provider="anthropic", anthropic_api_key="sk-test"))
        assert provider.name == "anthropic"


class TestAnthropicProvider:
    def _provider(self, handler) -> AnthropicProvider:
        return AnthropicProvider(
            api_key="sk-test", client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    def test_successful_call(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "About 50,000 customers lost power."}],
                    "usage": {"input_tokens": 400, "output_tokens": 20},
                },
            )

        generation = self._provider(handler).generate(_request())
        assert generation.ok
        assert generation.input_tokens == 400
        assert generation.prompt_version == "v1"

    def test_the_rules_are_sent_to_the_model(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            seen.update(_json.loads(request.content))
            return httpx.Response(
                200, json={"content": [{"type": "text", "text": "ok"}], "usage": {}}
            )

        self._provider(handler).generate(_request())
        assert "Invent nothing" in seen["system"]
        assert "Reuters" in seen["messages"][0]["content"]

    def test_an_api_error_is_returned_not_raised(self):
        generation = self._provider(
            lambda r: httpx.Response(429, text="rate limited")
        ).generate(_request())
        assert generation.ok is False and "429" in generation.error

    def test_the_api_key_never_appears_in_an_error(self):
        generation = self._provider(
            lambda r: httpx.Response(401, text="invalid key sk-test")
        ).generate(_request())
        assert "sk-test" not in generation.error

    def test_a_network_failure_is_returned_not_raised(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failure")

        generation = self._provider(handler).generate(_request())
        assert generation.ok is False and "ConnectError" in generation.error


# --- The service layer -------------------------------------------------------


_SLUGS = iter(range(1_000_000))


def _story(db, sources=3, headline="Dominion Energy outage hits Chesterfield County") -> Story:
    """Build a story covered by `sources` distinct publishers.

    Slugs are unique per call so a test can build two stories without colliding.
    """
    story = None
    batch = next(_SLUGS)
    for i in range(sources):
        source = Source(
            name=f"Pub {batch}-{i}",
            slug=f"ai-pub-{batch}-{i}",
            feed_url=f"https://p{batch}-{i}.example/rss",
        )
        db.add(source)
        db.flush()
        article = Article(
            source_id=source.id,
            original_guid=f"ai-{batch}-{i}",
            original_url=f"https://p{batch}-{i}.example/a",
            original_headline=headline,
            original_description="About 50,000 customers lost power after equipment failure.",
            original_published_at=NOW + timedelta(minutes=i * 10),
        )
        db.add(article)
        db.flush()
        decision = assign_story(db, article, now=NOW)
        story = db.get(Story, decision.story_id)
    rescore(db, story, now=NOW)
    return story


class _FakeProvider:
    """A provider that says exactly what a test tells it to."""

    name = "fake"
    model = "fake-1"

    def __init__(self, text, confidence=0.8, error=None, tokens=(100, 20)):
        self._text, self._confidence, self._error, self._tokens = text, confidence, error, tokens
        self.calls = 0

    def supports(self, operation):
        return True

    def generate(self, request):
        self.calls += 1
        return Generation(
            text=self._text,
            provider=self.name,
            model=self.model,
            confidence=self._confidence,
            error=self._error,
            input_tokens=self._tokens[0],
            output_tokens=self._tokens[1],
        )


class TestService:
    def test_generates_and_stores_with_provenance(self, db):
        """§26 — content, provider, model, timestamp, and the inputs used."""
        story = _story(db)
        outcome = generate_for_story(
            db, story, OPERATION_SUMMARY,
            provider=_FakeProvider("About 50,000 customers lost power."), now=NOW,
        )
        assert outcome.ok
        result = outcome.result
        assert result.provider == "fake" and result.is_current
        assert result.version == 1
        # §15: exactly which articles it was built from.
        assert len(result.inputs) == 3

    def test_unchanged_input_is_not_regenerated(self, db):
        """§28's cheapest saving."""
        story = _story(db)
        provider = _FakeProvider("About 50,000 customers lost power.")
        generate_for_story(db, story, OPERATION_SUMMARY, provider=provider, now=NOW)
        second = generate_for_story(db, story, OPERATION_SUMMARY, provider=provider, now=NOW)
        assert second.reused is True
        assert provider.calls == 1

    def test_regeneration_versions_rather_than_overwrites(self, db):
        """§26 — history survives."""
        story = _story(db)
        generate_for_story(
            db, story, OPERATION_SUMMARY, provider=_FakeProvider("First version."), now=NOW
        )
        generate_for_story(
            db, story, OPERATION_SUMMARY, provider=_FakeProvider("Second version."),
            force=True, now=NOW,
        )
        rows = db.query(AIResult).filter_by(story_id=story.id, operation=OPERATION_SUMMARY).all()
        assert len(rows) == 2
        assert sum(1 for r in rows if r.is_current) == 1
        current = next(r for r in rows if r.is_current)
        assert current.content == "Second version." and current.version == 2
        assert any(r.content == "First version." for r in rows)

    def test_a_human_edit_is_never_overwritten(self, db):
        """§35.4, Non-Negotiable #5."""
        story = _story(db)
        outcome = generate_for_story(
            db, story, OPERATION_SUMMARY, provider=_FakeProvider("Machine text."), now=NOW
        )
        outcome.result.edited_content = "An editor wrote this."
        db.flush()

        again = generate_for_story(
            db, story, OPERATION_SUMMARY,
            provider=_FakeProvider("Different machine text."),
            now=NOW + timedelta(hours=1),
        )
        assert again.reused is True
        assert again.result.edited_content == "An editor wrote this."

    def test_a_fabricated_figure_is_never_published(self, db):
        """The whole point of the guard, end to end (§15)."""
        story = _story(db)
        liar = _FakeProvider("About 65,000 customers lost power and 12 were injured.")
        outcome = generate_for_story(db, story, OPERATION_SUMMARY, provider=liar, now=NOW)

        assert outcome.ok  # we still produced something…
        assert "65,000" not in (outcome.result.content or "")  # …but not the invention
        assert outcome.result.provider == "extractive"  # fell back to real words

        flagged = db.query(ReviewQueueItem).filter_by(story_id=story.id).all()
        assert any(item.reason == "ai_guard_rejection" for item in flagged)

    def test_a_provider_failure_falls_back_rather_than_leaving_a_blank(self, db):
        story = _story(db)
        outcome = generate_for_story(
            db, story, OPERATION_SUMMARY,
            provider=_FakeProvider(None, error="HTTP 500"), now=NOW,
        )
        assert outcome.ok and outcome.result.provider == "extractive"

    def test_low_confidence_is_queued_for_review(self, db):
        """§27.1 — the confidence band that routes to a human."""
        story = _story(db)
        outcome = generate_for_story(
            db, story, OPERATION_SUMMARY,
            provider=_FakeProvider("About 50,000 customers lost power.",
                                   confidence=REVIEW_BELOW - 0.1),
            now=NOW,
        )
        assert outcome.result.review_state == "pending_review"
        assert db.query(ReviewQueueItem).filter_by(story_id=story.id).count() >= 1

    def test_usage_is_recorded_for_cost_control(self, db):
        """§28 — tokens, latency, and an estimated cost per call."""
        story = _story(db)
        generate_for_story(
            db, story, OPERATION_SUMMARY,
            provider=_FakeProvider("About 50,000 customers lost power."), now=NOW,
        )
        usage = db.query(AIUsage).filter_by(story_id=story.id).all()
        assert len(usage) == 1
        assert usage[0].input_tokens == 100 and usage[0].succeeded

    def test_budget_cap_blocks_spending(self, db):
        from app.config import Settings

        db.add(
            AIUsage(
                occurred_at=NOW, operation="generate_summary", provider="anthropic",
                model="m", estimated_cost_usd=9.0, succeeded=True,
            )
        )
        db.flush()
        ok, reason = within_budget(
            db, now=NOW + timedelta(hours=1),
            settings=Settings(ai_daily_cost_limit_usd=5.0),
        )
        assert ok is False and "daily AI budget" in reason

    def test_multi_source_story_gets_a_synthesis_single_source_a_summary(self, db):
        """§20 — synthesizing one report is just copying it."""
        multi = _story(db, sources=3)
        outcomes = enrich_story(db, multi, provider=_FakeProvider("Three outlets report this."),
                                now=NOW)
        assert OPERATION_SYNTHESIS in outcomes

        single = _story(db, sources=1, headline="Riverton Utilities outage in Marlow County")
        outcomes = enrich_story(db, single, provider=_FakeProvider("One outlet reports this."),
                                now=NOW)
        assert OPERATION_SUMMARY in outcomes

    def test_sources_are_exactly_the_story_articles(self, db):
        story = _story(db)
        assert len(sources_for_story(db, story)) == 3

    def test_a_story_with_no_articles_is_skipped(self, db):
        story = Story(state="emerging")
        db.add(story)
        db.flush()
        outcome = generate_for_story(db, story, OPERATION_SUMMARY, now=NOW)
        assert outcome.ok is False and "no visible articles" in outcome.skipped_reason

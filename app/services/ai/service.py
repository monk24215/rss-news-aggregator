"""The AI service layer — §16, §26, §27, §28.

Everything the application asks of AI goes through here, and this module owns the four
things a provider must never be trusted with:

  **Provider choice** (§16). Configured, not hardcoded, with the extractive provider as
  a fallback that always works.

  **Versioning** (§26). A regeneration inserts a new `ai_results` row and moves
  `is_current`. Nothing is ever updated in place, so a previous version — and any human
  edit of it — survives.

  **Cost control** (§28). Usage is recorded per call; daily and monthly caps are checked
  before spending; and an unchanged input is never regenerated, which is the cheapest
  possible saving.

  **The guard** (§15). Output is checked against the source reporting before it is
  stored. A hard failure falls back to the extractive provider rather than publishing;
  a soft warning is stored but routed for review (§27.2).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AIResult,
    AIResultInput,
    AIUsage,
    Article,
    ReviewQueueItem,
    Source,
    Story,
)
from app.services.ai import guard
from app.services.ai.base import (
    OPERATION_HEADLINE,
    OPERATION_SUMMARY,
    OPERATION_SYNTHESIS,
    AIProvider,
    Generation,
    GenerationRequest,
    SourceText,
)
from app.services.ai.extractive import ExtractiveProvider

logger = logging.getLogger("ai")

#: Rough per-million-token prices used only for the cost *estimate* in §28's dashboard.
#: Wrong-but-visible beats absent: an operator needs an order of magnitude, and a stale
#: number they can see beats a precise one they cannot.
PRICING_USD_PER_MTOK = {
    "anthropic": {"input": 3.0, "output": 15.0},
}

#: Confidence at or below which a result is queued for a human (§27.1).
REVIEW_BELOW = 0.6


@dataclass
class AIOutcome:
    """What happened when the service was asked for something."""

    result: AIResult | None
    generation: Generation | None
    reused: bool = False
    skipped_reason: str | None = None
    guard_result: guard.GuardResult | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


def build_provider(settings=None) -> AIProvider:
    """Select the configured provider, falling back to extractive (§16)."""
    settings = settings or get_settings()
    choice = (settings.ai_provider or "extractive").strip().lower()

    if choice in ("anthropic", "claude"):
        key = (settings.anthropic_api_key or "").strip()
        if not key:
            logger.warning("AI_PROVIDER=anthropic but no ANTHROPIC_API_KEY; using extractive")
            return ExtractiveProvider()
        from app.services.ai.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=key, model=settings.ai_model)

    return ExtractiveProvider()


def _fingerprint(request: GenerationRequest) -> str:
    """Identity of the inputs, so unchanged content is never regenerated (§28)."""
    material = "|".join(
        [request.operation, request.length, request.style or ""]
        + [f"{s.article_id}:{s.headline}:{s.description or ''}" for s in request.sources]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _estimate_cost(generation: Generation) -> float | None:
    prices = PRICING_USD_PER_MTOK.get(generation.provider)
    if not prices or generation.input_tokens is None:
        return None
    return round(
        (generation.input_tokens / 1_000_000) * prices["input"]
        + ((generation.output_tokens or 0) / 1_000_000) * prices["output"],
        6,
    )


def spend_since(session: Session, since: datetime) -> float:
    total = session.execute(
        select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0)).where(
            AIUsage.occurred_at >= since
        )
    ).scalar_one()
    return float(total or 0)


def within_budget(session: Session, now: datetime | None = None, settings=None) -> tuple[bool, str]:
    """Check the daily and monthly caps before spending anything (§28)."""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)

    daily_cap = settings.ai_daily_cost_limit_usd
    monthly_cap = settings.ai_monthly_cost_limit_usd

    if daily_cap and daily_cap > 0:
        spent = spend_since(session, now - timedelta(days=1))
        if spent >= daily_cap:
            return False, f"daily AI budget reached (${spent:.2f} of ${daily_cap:.2f})"
    if monthly_cap and monthly_cap > 0:
        spent = spend_since(session, now - timedelta(days=30))
        if spent >= monthly_cap:
            return False, f"monthly AI budget reached (${spent:.2f} of ${monthly_cap:.2f})"
    return True, ""


def sources_for_story(session: Session, story: Story) -> list[SourceText]:
    """The reporting a generation is allowed to use — and nothing else (§15)."""
    rows = session.execute(
        select(Article, Source)
        .join(Source, Source.id == Article.source_id)
        .where(Article.story_id == story.id, Article.is_hidden.is_(False))
        .order_by(Article.original_published_at.asc().nullslast())
    ).all()
    return [
        SourceText(
            article_id=article.id,
            publication=source.name,
            headline=article.original_headline,
            description=article.original_description,
            url=article.original_url,
        )
        for article, source in rows
    ]


def _current(session: Session, story_id: int, operation: str) -> AIResult | None:
    return session.execute(
        select(AIResult).where(
            AIResult.story_id == story_id,
            AIResult.operation == operation,
            AIResult.is_current.is_(True),
        )
    ).scalar_one_or_none()


def generate_for_story(
    session: Session,
    story: Story,
    operation: str,
    *,
    provider: AIProvider | None = None,
    length: str = "standard",
    style: str | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> AIOutcome:
    """Generate (or reuse) one artifact for a story, recording everything."""
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    provider = provider or build_provider(settings)

    sources = sources_for_story(session, story)
    if not sources:
        return AIOutcome(None, None, skipped_reason="story has no visible articles")

    request = GenerationRequest(
        operation=operation, sources=sources, length=length, style=style
    )
    fingerprint = _fingerprint(request)

    existing = _current(session, story.id, operation)
    if existing is not None and not force:
        # §28: do not regenerate unchanged content.
        if existing.input_fingerprint == fingerprint:
            return AIOutcome(existing, None, reused=True)
        # §35.4 / Non-Negotiable #5: an editor's text is not overwritten by a rerun.
        if existing.edited_content:
            return AIOutcome(
                existing, None, reused=True, skipped_reason="human edit preserved"
            )

    affordable, budget_reason = within_budget(session, now, settings)
    if not affordable and not isinstance(provider, ExtractiveProvider):
        logger.warning("%s — falling back to extractive", budget_reason)
        provider = ExtractiveProvider()

    generation = provider.generate(request)

    # A model that errors must not leave the story blank: fall back to words the
    # publishers already wrote.
    if not generation.ok and not isinstance(provider, ExtractiveProvider):
        logger.warning("provider %s failed (%s); falling back", provider.name, generation.error)
        _record_usage(session, generation, story, operation, now, succeeded=False)
        generation = ExtractiveProvider().generate(request)

    if not generation.ok:
        _record_usage(session, generation, story, operation, now, succeeded=False)
        return AIOutcome(None, generation, skipped_reason=generation.error or "no output")

    checked = guard.check(generation.text or "", request.corpus)
    if not checked.ok and not isinstance(provider, ExtractiveProvider):
        # §15 hard failure — do not publish a fabricated figure or quotation.
        logger.error(
            "guard rejected %s output for story %s: %s", provider.name, story.id, checked.summary
        )
        _record_usage(session, generation, story, operation, now, succeeded=False)
        session.add(
            ReviewQueueItem(
                reason="ai_guard_rejection",
                story_id=story.id,
                confidence=0.0,
                detail={
                    "operation": operation,
                    "provider": generation.provider,
                    "warnings": checked.warnings,
                    "rejected_text": (generation.text or "")[:500],
                },
            )
        )
        generation = ExtractiveProvider().generate(request)
        if not generation.ok:
            return AIOutcome(None, generation, guard_result=checked)
        checked = guard.check(generation.text or "", request.corpus)

    confidence = guard.adjust_confidence(generation.confidence, checked)
    result = _store(session, story, operation, generation, fingerprint, confidence, now, request)
    _record_usage(session, generation, story, operation, now, succeeded=True)

    if confidence <= REVIEW_BELOW or checked.warnings:
        session.add(
            ReviewQueueItem(
                reason="low_confidence_ai" if not checked.warnings else "ai_guard_warning",
                story_id=story.id,
                ai_result_id=result.id,
                confidence=confidence,
                detail={"operation": operation, "warnings": checked.warnings},
            )
        )
        result.review_state = "pending_review"

    session.flush()
    return AIOutcome(result, generation, guard_result=checked)


def _store(
    session: Session,
    story: Story,
    operation: str,
    generation: Generation,
    fingerprint: str,
    confidence: float,
    now: datetime,
    request: GenerationRequest,
) -> AIResult:
    """Insert a new version and move the pointer — never update in place (§26)."""
    previous = _current(session, story.id, operation)
    version = 1
    if previous is not None:
        previous.is_current = False
        previous.review_state = "superseded"
        version = (previous.version or 1) + 1
        session.flush()  # release the partial unique index before inserting the new row

    result = AIResult(
        target_type="story",
        story_id=story.id,
        operation=operation,
        content=generation.text,
        payload=generation.payload,
        provider=generation.provider,
        model=generation.model,
        prompt_version=generation.prompt_version,
        generated_at=now,
        input_fingerprint=fingerprint,
        version=version,
        is_current=True,
        confidence=confidence,
        review_state="auto_accepted",
    )
    session.add(result)
    session.flush()

    # §15: record exactly which articles this was generated from.
    for article_id in request.article_ids:
        session.add(AIResultInput(ai_result_id=result.id, article_id=article_id))
    session.flush()
    return result


def _record_usage(
    session: Session,
    generation: Generation,
    story: Story,
    operation: str,
    now: datetime,
    succeeded: bool,
) -> None:
    """One row per call, successful or not (§28)."""
    session.add(
        AIUsage(
            occurred_at=now,
            operation=operation,
            provider=generation.provider,
            model=generation.model,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
            estimated_cost_usd=_estimate_cost(generation),
            latency_ms=generation.latency_ms,
            succeeded=succeeded,
            error=generation.error,
            story_id=story.id,
        )
    )


def enrich_story(
    session: Session,
    story: Story,
    *,
    provider: AIProvider | None = None,
    length: str = "standard",
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, AIOutcome]:
    """Generate the full presentation set for one story (§17, §18, §20).

    Multi-source stories get a synthesis (§20); single-source stories get a plain
    summary, because "synthesizing" one report is just copying it.
    """
    provider = provider or build_provider()
    outcomes: dict[str, AIOutcome] = {}

    outcomes[OPERATION_HEADLINE] = generate_for_story(
        session, story, OPERATION_HEADLINE, provider=provider, force=force, now=now
    )

    summary_operation = (
        OPERATION_SYNTHESIS if (story.source_count or 0) > 1 else OPERATION_SUMMARY
    )
    outcomes[summary_operation] = generate_for_story(
        session, story, summary_operation, provider=provider, length=length, force=force, now=now
    )
    return outcomes

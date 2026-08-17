"""Entity extraction and tagging without a model — §8, §19.

v1 ships these as deterministic rules rather than AI calls, for two reasons. They run on
every article, so they are the most expensive place to put a model; and they are the
inputs to clustering, so a non-deterministic result here would make story assignment
unreproducible and untestable.

The AI layer improves on this later (§16 lists `extract_entities` and `classify_article`
as operations) — it replaces these functions rather than working around them, and until
a key is configured these are what runs.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, ArticleTag, Tag
from app.services.normalize import strip_html

#: Words that start a sentence or appear capitalized without naming anything.
_STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those there here when where while
    what which who whom whose why how all any both each few more most other some such
    no nor not only own same so too very can will just should now also after before
    during over under again further once about against between into through above below
    from up down out off on in at by for with to of as is are was were be been being
    has have had do does did doing i you he she it we they them his her its their our
    said says say new news report reports according update updates breaking exclusive
    monday tuesday wednesday thursday friday saturday sunday january february march
    april may june july august september october november december
    """.split()
)

#: A capitalized run: "Power Grid", "Reuters", "New York City".
_PROPER_NOUN = re.compile(r"\b([A-Z][\w&'’.-]*(?:\s+[A-Z][\w&'’.-]*){0,4})\b")

#: Cheap kind hints. Not linguistics — just enough to make clustering signals distinct.
_ORG_MARKERS = re.compile(
    r"\b(inc|corp|corporation|company|co|ltd|llc|plc|group|authority|commission|"
    r"department|agency|administration|bureau|committee|council|university|institute|"
    r"association|union|bank|utility|electric|energy|power|systems|technologies)\b",
    re.IGNORECASE,
)
_PLACE_MARKERS = re.compile(
    r"\b(county|city|state|province|region|district|valley|river|island|coast|north|"
    r"south|east|west|northern|southern|eastern|western)\b",
    re.IGNORECASE,
)
_PERSON_HINT = re.compile(r"\b(mr|mrs|ms|dr|gov|governor|sen|senator|rep|president|"
                          r"mayor|chief|director|secretary|minister|judge|officer)\b\.?\s",
                          re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedEntity:
    kind: str
    value: str
    normalized_value: str
    confidence: float


def _classify(phrase: str, context: str) -> str:
    if _ORG_MARKERS.search(phrase):
        return "organization"
    if _PLACE_MARKERS.search(phrase):
        return "location"
    window = context[max(0, context.find(phrase) - 20) : context.find(phrase)]
    if _PERSON_HINT.search(window) or len(phrase.split()) == 2 and phrase.isupper() is False:
        return "person" if _PERSON_HINT.search(window) else "other"
    return "other"


def extract_entities(headline: str, description: str | None = None) -> list[ExtractedEntity]:
    """Pull proper nouns out of a headline and summary (§19).

    Confidence reflects where and how often a phrase appeared: a multi-word proper noun
    in the headline is a stronger clustering signal than a single capitalized word
    buried in a summary, and saying so lets the caller weight it.
    """
    headline = strip_html(headline) or ""
    description = strip_html(description) or ""
    seen: dict[str, ExtractedEntity] = {}

    for text, weight in ((headline, 1.0), (description, 0.6)):
        if not text:
            continue
        for match in _PROPER_NOUN.finditer(text):
            phrase = match.group(1).strip(" .,-")
            words = phrase.split()
            # A single stopword-ish capitalized word is usually just sentence case.
            if not words or all(w.lower() in _STOPWORDS for w in words):
                continue
            if len(words) == 1 and (len(phrase) < 4 or phrase.lower() in _STOPWORDS):
                continue
            normalized = " ".join(w.lower() for w in words)
            confidence = min(1.0, weight * (0.6 + 0.2 * min(len(words), 3)))
            existing = seen.get(normalized)
            if existing is None or confidence > existing.confidence:
                seen[normalized] = ExtractedEntity(
                    kind=_classify(phrase, text),
                    value=phrase[:300],
                    normalized_value=normalized[:300],
                    confidence=round(confidence, 3),
                )
    return sorted(seen.values(), key=lambda e: (-e.confidence, e.normalized_value))


def suggest_tags(session: Session, article: Article) -> list[tuple[Tag, float]]:
    """Match an article against the tag vocabulary (§8).

    A tag matches when its name (or any word of a multi-word name) appears in the
    headline or summary. Confidence is higher for a headline match than a summary match,
    which is what feeds §27.1's automation thresholds later.

    The AI may suggest tags, but administrators approve them (§8) — so every tag this
    creates is marked `is_manual=False`, leaving the human record clean.
    """
    haystack_title = (article.normalized_title or "").lower()
    haystack_body = (strip_html(article.original_description) or "").lower()
    if not haystack_title and not haystack_body:
        return []

    tags = session.execute(select(Tag).where(Tag.is_active.is_(True))).scalars().all()
    matches: list[tuple[Tag, float]] = []
    for tag in tags:
        needle = tag.name.lower().strip()
        if not needle:
            continue
        if needle in haystack_title:
            matches.append((tag, 0.95))
        elif needle in haystack_body:
            matches.append((tag, 0.7))
        elif len(needle.split()) > 1:
            # Partial match on a multi-word tag: every word present somewhere.
            words = needle.split()
            combined = f"{haystack_title} {haystack_body}"
            if all(w in combined for w in words):
                matches.append((tag, 0.55))
    return matches


def apply_tags(session: Session, article: Article) -> int:
    """Attach suggested tags to an article, never overwriting a human's decision.

    Idempotent: re-running assigns nothing new, which is what makes the stage safe to
    retry (Non-Negotiable #8).
    """
    existing = {
        row.tag_id
        for row in session.execute(
            select(ArticleTag).where(ArticleTag.article_id == article.id)
        ).scalars()
    }
    added = 0
    for tag, confidence in suggest_tags(session, article):
        if tag.id in existing:
            continue
        session.add(
            ArticleTag(
                article_id=article.id, tag_id=tag.id, confidence=confidence, is_manual=False
            )
        )
        added += 1
    return added


def top_keywords(text: str, limit: int = 8) -> list[str]:
    """Frequent meaningful words — a cheap similarity signal for clustering."""
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 3 and w not in _STOPWORDS
    ]
    return [word for word, _ in Counter(words).most_common(limit)]

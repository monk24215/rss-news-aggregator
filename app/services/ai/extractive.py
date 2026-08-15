"""The provider that needs no API key — §16, and what makes v1 shippable.

Everything it produces is built from words the publishers already wrote, selected and
arranged rather than generated. That is a real constraint with a real payoff: it cannot
violate §15 because it never composes a new claim. It also cannot paraphrase, so its
output is plainer than a model's.

This is the default provider. Configuring `AI_PROVIDER=anthropic` with a key swaps it
out per §16 without any other change — and the extractive provider stays as the fallback
when the model is unavailable, over budget, or its output fails the guard, so the site
never has a blank where a summary should be.
"""

from __future__ import annotations

import re
import time
from collections import Counter

from app.services.ai.base import (
    OPERATION_CONFLICTS,
    OPERATION_HEADLINE,
    OPERATION_SUMMARY,
    OPERATION_SYNTHESIS,
    Generation,
    GenerationRequest,
)
from app.services.enrich import _STOPWORDS
from app.services.normalize import strip_html

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_NUMBER_CLAIM = re.compile(r"\b(\d[\d,.]*)\s*([a-z%][a-z\s]{0,24})?", re.IGNORECASE)

LENGTH_SENTENCES = {"short": 1, "standard": 2, "detailed": 4}


def _sentences(text: str) -> list[str]:
    cleaned = strip_html(text) or ""
    return [s.strip() for s in _SENTENCE.split(cleaned) if len(s.strip()) > 25]


def _score_sentence(sentence: str, term_weights: Counter) -> float:
    words = [w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if w not in _STOPWORDS]
    if not words:
        return 0.0
    score = sum(term_weights.get(w, 0) for w in words) / (len(words) ** 0.6)
    # A sentence carrying a figure usually carries the news.
    if re.search(r"\d", sentence):
        score *= 1.15
    return score


class ExtractiveProvider:
    """Selects the most representative sentences across the reporting."""

    name = "extractive"
    model = "extractive-v1"

    def supports(self, operation: str) -> bool:
        return operation in {
            OPERATION_HEADLINE,
            OPERATION_SUMMARY,
            OPERATION_SYNTHESIS,
            OPERATION_CONFLICTS,
        }

    def generate(self, request: GenerationRequest) -> Generation:
        started = time.perf_counter()
        try:
            if request.operation == OPERATION_HEADLINE:
                text, confidence, payload = self._headline(request)
            elif request.operation in (OPERATION_SUMMARY, OPERATION_SYNTHESIS):
                text, confidence, payload = self._summary(request)
            elif request.operation == OPERATION_CONFLICTS:
                text, confidence, payload = self._conflicts(request)
            else:
                return Generation(
                    None, self.name, self.model, error=f"unsupported operation {request.operation}"
                )
        except Exception as exc:  # a provider must never take the pipeline down
            return Generation(None, self.name, self.model, error=f"{type(exc).__name__}: {exc}")

        return Generation(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_version="extractive-1",
            confidence=confidence,
            latency_ms=int((time.perf_counter() - started) * 1000),
            payload=payload,
        )

    # --- operations ----------------------------------------------------------

    def _headline(self, request: GenerationRequest) -> tuple[str, float, dict]:
        """Pick the clearest existing headline rather than write a new one (§17).

        §17 wants concision and clarity without invention. With several outlets covering
        one event, one of them has usually already written the clearest sentence — so we
        choose it and say whose it is, instead of composing a fifth version nobody
        published.
        """
        candidates = [s for s in request.sources if s.headline]
        if not candidates:
            return "", 0.0, {}

        terms = self._term_weights(request)
        best = max(
            candidates,
            key=lambda s: (
                _score_sentence(s.headline, terms)
                # Prefer a headline that is informative but not a run-on.
                * (1.0 if 40 <= len(s.headline) <= 110 else 0.85)
            ),
        )
        confidence = 0.75 if len(candidates) > 1 else 0.6
        return best.headline, confidence, {"chosen_from": best.publication, "method": "selected"}

    def _summary(self, request: GenerationRequest) -> tuple[str, float, dict]:
        """Select the sentences that most of the coverage agrees on (§18, §20)."""
        terms = self._term_weights(request)
        pool: list[tuple[float, str, str]] = []
        for source in request.sources:
            for sentence in _sentences(source.combined):
                pool.append((_score_sentence(sentence, terms), sentence, source.publication))
        if not pool:
            return "", 0.0, {}

        wanted = LENGTH_SENTENCES.get(request.length, 2)
        chosen: list[tuple[float, str, str]] = []
        seen_terms: set[str] = set()
        for score, sentence, publication in sorted(pool, key=lambda p: -p[0]):
            key_terms = {
                w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if w not in _STOPWORDS
            }
            # Skip a sentence that repeats what we already have — several outlets say
            # the same thing, and a summary that says it three times is worse than one.
            if key_terms and len(key_terms & seen_terms) / len(key_terms) > 0.6:
                continue
            chosen.append((score, sentence, publication))
            seen_terms |= key_terms
            if len(chosen) >= wanted:
                break

        text = " ".join(sentence for _, sentence, _ in chosen)
        confidence = min(0.5 + 0.1 * len(request.sources), 0.85)
        return (
            text,
            confidence,
            {
                "method": "extractive",
                "sentences": len(chosen),
                "publications": sorted({p for _, _, p in chosen}),
            },
        )

    def _conflicts(self, request: GenerationRequest) -> tuple[str, float, dict]:
        """Find figures the sources disagree about (§24).

        Deliberately narrow: numeric disagreement about the same subject is the one kind
        of conflict that can be detected without judgement. "Sources disagree about the
        cause" needs reading comprehension; "one says 50,000 and another says 65,000"
        needs arithmetic.
        """
        claims: dict[str, dict[str, list[str]]] = {}
        for source in request.sources:
            for match in _NUMBER_CLAIM.finditer(source.combined):
                value, unit = match.group(1), (match.group(2) or "").strip().lower()
                subject = " ".join(unit.split()[:3])
                if not subject or len(subject) < 3:
                    continue
                claims.setdefault(subject, {}).setdefault(value, []).append(source.publication)

        conflicts = [
            {
                "subject": subject,
                "values": [
                    {"value": value, "publications": sorted(set(pubs))}
                    for value, pubs in values.items()
                ],
            }
            for subject, values in claims.items()
            if len(values) > 1
        ]
        if not conflicts:
            return "", 0.9, {"conflicts": []}

        lines = [
            f"{c['subject']}: "
            + " vs ".join(
                f"{v['value']} ({', '.join(v['publications'])})" for v in c["values"][:3]
            )
            for c in conflicts[:3]
        ]
        return "Sources disagree — " + "; ".join(lines), 0.6, {"conflicts": conflicts}

    # --- helpers -------------------------------------------------------------

    def _term_weights(self, request: GenerationRequest) -> Counter:
        """Terms weighted by how many publications use them.

        A word several outlets independently reach for is more likely to be the story;
        a word only one uses is more likely to be that outlet's angle.
        """
        weights: Counter = Counter()
        for source in request.sources:
            terms = {
                w
                for w in re.findall(r"[a-z0-9]+", source.combined.lower())
                if len(w) > 3 and w not in _STOPWORDS
            }
            weights.update(terms)
        return weights

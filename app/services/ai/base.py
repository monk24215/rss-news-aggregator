"""The AI service contract — §16.

The application asks for an *operation* ("generate a headline for this article") and
never for a provider. That indirection is what §16 requires, and it is what lets v1 ship
with no API key: the extractive provider satisfies the same contract using only the
publisher's own words.

A provider returns a `Generation`, not a string, because the caller needs to record who
produced it, from what, and at what cost (§26, §28) — and because a provider that cannot
answer must be able to say so rather than invent something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: The operations §16 lists. Providers may support a subset; the service layer routes.
OPERATION_HEADLINE = "generate_headline"
OPERATION_SUMMARY = "generate_summary"
OPERATION_SYNTHESIS = "synthesize_story"
OPERATION_CONFLICTS = "identify_conflicts"
OPERATION_CLASSIFY = "classify_article"
OPERATION_ENTITIES = "extract_entities"


@dataclass(frozen=True)
class SourceText:
    """One piece of reporting handed to a provider.

    Carries its article id so the result can record exactly what it was built from
    (§15: "store the source articles used to generate every synthesized story").
    """

    article_id: int
    publication: str
    headline: str
    description: str | None = None
    url: str | None = None

    @property
    def combined(self) -> str:
        return f"{self.headline}. {self.description}" if self.description else self.headline


@dataclass(frozen=True)
class GenerationRequest:
    operation: str
    sources: list[SourceText]
    #: "short" | "standard" | "detailed" (§18)
    length: str = "standard"
    style: str | None = None

    @property
    def article_ids(self) -> list[int]:
        return [s.article_id for s in self.sources]

    @property
    def corpus(self) -> str:
        """Everything the provider is allowed to draw on, as one string.

        The guard checks generated text against exactly this — so if it is not in here,
        the model is not permitted to say it.
        """
        return "\n\n".join(s.combined for s in self.sources)


@dataclass(frozen=True)
class Generation:
    """What a provider produced, and what it cost."""

    text: str | None
    provider: str
    model: str
    prompt_version: str | None = None
    confidence: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    payload: dict | None = None
    #: Set by the guard, not the provider — see `app/services/ai/guard.py`.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text) and self.error is None


@runtime_checkable
class AIProvider(Protocol):
    """What every provider must implement."""

    name: str
    model: str

    def supports(self, operation: str) -> bool: ...

    def generate(self, request: GenerationRequest) -> Generation: ...

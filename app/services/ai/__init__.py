"""The AI layer — §16.

Import `build_provider` and `enrich_story` from here; the provider implementations are
an internal detail, which is the point of §16's abstraction.
"""

from app.services.ai.base import (
    OPERATION_CLASSIFY,
    OPERATION_CONFLICTS,
    OPERATION_ENTITIES,
    OPERATION_HEADLINE,
    OPERATION_SUMMARY,
    OPERATION_SYNTHESIS,
    AIProvider,
    Generation,
    GenerationRequest,
    SourceText,
)
from app.services.ai.extractive import ExtractiveProvider
from app.services.ai.service import (
    AIOutcome,
    build_provider,
    enrich_story,
    generate_for_story,
    sources_for_story,
    spend_since,
    within_budget,
)

__all__ = [
    "AIProvider",
    "AIOutcome",
    "Generation",
    "GenerationRequest",
    "SourceText",
    "ExtractiveProvider",
    "build_provider",
    "enrich_story",
    "generate_for_story",
    "sources_for_story",
    "spend_since",
    "within_budget",
    "OPERATION_HEADLINE",
    "OPERATION_SUMMARY",
    "OPERATION_SYNTHESIS",
    "OPERATION_CONFLICTS",
    "OPERATION_CLASSIFY",
    "OPERATION_ENTITIES",
]

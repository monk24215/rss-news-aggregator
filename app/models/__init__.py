"""ORM models for the AI RSS News Aggregator.

Organized by the three data layers in §4.2 plus operations:

    sources.py   Layer 1/2  sources, source types, tags, fetch logs
    content.py   Layer 1/2  articles, stories, membership, entities, claims, timeline
    ai.py        Layer 3    versioned AI results, review queue, cost accounting
    feeds.py     Layer 3    feed instances and their rules
    people.py    ops        users, saved views, reader preferences, settings
    ops.py       ops        jobs, system logs, audit log, change history, notifications

Import models from this package (`from app.models import Article`) rather than from the
submodules, so the layout can change without touching call sites.
"""

from app.models.ai import AIResult, AIResultInput, AIUsage, ReviewQueueItem
from app.models.base import (
    LAYER_ANALYSIS,
    LAYER_OPERATIONS,
    LAYER_ORIGINAL,
    LAYER_PRESENTATION,
    LAYERS,
    Base,
    TimestampMixin,
)
from app.models.content import (
    Article,
    ArticleEntity,
    ArticleTag,
    ScoringFactor,
    Story,
    StoryArticle,
    StoryClaim,
    StoryClaimValue,
    StoryTag,
    StoryTimelineEvent,
)
from app.models.feeds import (
    FeedInstance,
    FeedInstanceEntry,
    FeedInstanceSource,
    FeedInstanceSourceType,
    FeedInstanceTag,
)
from app.models.ops import (
    AuditLog,
    ChangeHistory,
    Heartbeat,
    Notification,
    ProcessingJob,
    SystemLog,
)
from app.models.people import ReaderPreference, SavedView, Setting, User
from app.models.sources import Source, SourceFetchLog, SourceTag, SourceType, Tag

#: Columns holding publisher-owned data. The migration's trigger protects exactly these;
#: the test suite asserts the two lists agree, so adding an `original_` column without
#: protecting it fails CI rather than silently creating a hole in Non-Negotiable #1.
IMMUTABLE_ORIGINAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "articles": (
        "original_guid",
        "original_url",
        "original_canonical_url",
        "original_headline",
        "original_description",
        "original_author",
        "original_image_url",
        "original_published_at",
        "original_payload",
    ),
}

__all__ = [
    # base
    "Base",
    "TimestampMixin",
    "LAYERS",
    "LAYER_ORIGINAL",
    "LAYER_ANALYSIS",
    "LAYER_PRESENTATION",
    "LAYER_OPERATIONS",
    "IMMUTABLE_ORIGINAL_COLUMNS",
    # sources
    "Source",
    "SourceType",
    "SourceTag",
    "SourceFetchLog",
    "Tag",
    # content
    "Article",
    "ArticleEntity",
    "ArticleTag",
    "Story",
    "StoryArticle",
    "StoryTag",
    "StoryTimelineEvent",
    "StoryClaim",
    "StoryClaimValue",
    "ScoringFactor",
    # ai
    "AIResult",
    "AIResultInput",
    "ReviewQueueItem",
    "AIUsage",
    # feeds
    "FeedInstance",
    "FeedInstanceTag",
    "FeedInstanceSource",
    "FeedInstanceSourceType",
    "FeedInstanceEntry",
    # people
    "User",
    "SavedView",
    "ReaderPreference",
    "Setting",
    # ops
    "Heartbeat",
    "ProcessingJob",
    "SystemLog",
    "AuditLog",
    "ChangeHistory",
    "Notification",
]

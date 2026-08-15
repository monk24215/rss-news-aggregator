# AI RSS News Aggregator
## Product and Development Specification — Version 2.0 (Consolidated)

**Status:** Consolidated master specification
**Supersedes:** Versions 1.01, 1.02, 1.03
**Date:** August 2026

---

## About This Document

This is the single authoritative specification for the AI RSS News Aggregator. It consolidates the original product specification (v1.02) and the later UX, operational, and infrastructure additions (v1.01 / v1.03, which are identical to each other) into one coherent document.

Nothing has been removed. Requirements that appeared twice — the product principle, the build order, the data model, feed configuration, the story page — have been reconciled into a single home each. Section numbering has been reorganized by theme; **Appendix C** maps every old section number to its new location.

**How to read it:**

| If you are | Start at |
| --- | --- |
| Deciding what to build | Part I — Product Foundation |
| Designing the schema | Part II — Data Model and Domain |
| Building ingestion | Part III — Collection and Processing |
| Building the AI layer | Part IV — Intelligence Layer |
| Building feeds and output | Part V — Feeds, Distribution, and Discovery |
| Building screens | Part VI — Interfaces |
| Setting up infrastructure | Part VII — Platform and Operations |
| Planning the work | Part VIII — Delivery |

Throughout: **must** marks a hard requirement, **should** marks a strong default that may be adjusted with reason, and **may** marks an option.

---

# Part I — Product Foundation

## 1. What We Are Building

Build a modern RSS/Atom news aggregation platform that collects articles from many sources, organizes them by category and tag, uses AI to improve headlines and summaries, detects duplicate and related coverage, combines multiple reports into developing stories, identifies trending topics, and produces customizable aggregated feeds.

The application is a polished news platform, not a basic RSS reader.

## 2. The Governing Principle

The platform must not simply answer:

> "What RSS articles were published?"

It must answer:

> "What is happening, which stories matter, what are multiple sources reporting, what has changed, where do reports disagree, and where can I read the original reporting?"

From this follow four rules that govern every decision in this document:

1. **Articles are the raw material. Stories are the primary information unit.**
2. **AI improves organization, presentation, comparison, discovery, and synthesis — original sources remain the authority.**
3. **The product should feel like a curated, intelligent news platform, not an RSS feed directory.**
4. **It should feel simple to operate despite the complexity underneath.** Complexity belongs in the system where it is useful, not in the user's face.

## 3. Core Objects

The system has four primary objects.

| Object | Definition |
| --- | --- |
| **Source** | A website or publication that provides RSS/Atom feeds. |
| **Article** | An individual item retrieved from a source. |
| **Story** | An underlying event or topic that may be covered by many articles from different sources. |
| **Feed Instance** | A customized news feed built from selected tags, sources, categories, and filtering rules. |

The key distinction is that **multiple articles can belong to one story.**

For example: Reuters, AP, a local newspaper, and an industry publication may all report the same power outage. The system must recognize these as four source articles belonging to one developing story rather than displaying four unrelated stories.

## 4. Architecture Principles

### 4.1 One Canonical Article and Story Model

Everything is built around a single canonical article/story data model.

Public pages, administrative interfaces, RSS output, APIs, email, notifications, and future integrations all consume the same underlying article and story services. **No output channel maintains its own independent copy of article or story data.**

### 4.2 Three Separated Data Layers

Maintain three distinct conceptual layers. This separation is the backbone of the system's integrity guarantees.

**Layer 1 — Original Data (immutable)**

- Source name
- Original headline
- Original URL
- Original publication date
- Original metadata
- Original source information

**Layer 2 — System Analysis**

- Tags
- Story assignment
- Importance score
- Trending score
- Entities
- Duplicate status
- AI confidence
- Conflict information

**Layer 3 — Presentation**

- AI headline
- AI summary
- Story synthesis
- Feed-specific presentation

**The system must never overwrite original source information with AI-generated or system-derived information.** Layer 1 is written once at ingestion and read forever after.

### 4.3 Output Independence

Adding an output channel must never require rebuilding the article or story processing system. Every channel consumes the same canonical services.

Current and potential channels:

- Public website
- RSS
- Public API
- Email newsletters
- Browser notifications
- Mobile applications
- Telegram
- Discord
- Slack
- Social media posts
- Webhooks

### 4.4 Nothing Expensive on the Request Path

The public interface must never wait for AI processing, feed fetching, or clustering. All expensive work happens asynchronously in background workers. See §14 and §32.

## 5. Primary Workflows

The application is designed around clear workflows rather than exposing every underlying capability at once. The interface should make these workflows obvious without surfacing unnecessary complexity.

### 5.1 Administrator

1. Add a source.
2. Test and preview the source.
3. Configure source type and tags.
4. Activate the source.
5. Monitor source health.
6. Review AI decisions that need attention.
7. Create a feed.
8. Preview the feed.
9. Publish the feed.
10. Monitor stories, trends, processing, and system health.

### 5.2 Reader

1. Land on the platform.
2. Immediately understand what is happening.
3. Scan trending and latest stories.
4. Open a story.
5. Understand the event without reading multiple source articles.
6. See which sources reported it.
7. See important differences or conflicts between reports.
8. Follow the original reporting.

### 5.3 System Operator

1. Fetch sources.
2. Normalize articles.
3. Detect duplicates.
4. Tag and classify articles.
5. Find related stories.
6. Generate AI content.
7. Calculate importance and trending scores.
8. Publish results.
9. Retry individual failed stages.
10. Monitor queues, errors, source health, and system performance.

---

# Part II — Data Model and Domain

## 6. Entities

At minimum, create the following tables/entities.

**Core content**

- `sources`
- `articles`
- `stories`
- `story_articles`
- `tags`
- `source_tags`
- `article_tags`

**Distribution**

- `feed_instances`
- `feed_instance_tags`
- `feed_instance_sources`

**Intelligence**

- `ai_results` (versioned — see §26)
- `entities` / article entity extractions
- `story_claims` and conflict records (see §24)
- `story_timeline_events` (see §22)
- `scoring_factors` (see §25)

**Operations**

- `processing_jobs`
- `source_fetch_logs`
- `system_logs`
- `audit_logs` (distinct from system logs — see §37)
- `change_history` (previous/new values for undo — see §36)
- `ai_usage` / cost records (see §28)

**People and configuration**

- `users` (with roles — see §35)
- `saved_views` (see §38)
- `reader_preferences` (see §31)
- `settings`

Data-model rules:

- Keep AI-generated information separate from original source information.
- Never overwrite original article data with AI-generated content.
- Store the source articles used to generate every synthesized story.
- Store individual scoring factors, not only the final score.

## 7. Sources

Each source record contains:

**Identity**

- Source name
- Website URL
- RSS/Atom feed URL
- Description
- Logo/image
- Source type
- Tags (multiple allowed)
- Priority

**Operational state**

- Active/inactive status
- Fetch frequency
- Last successful fetch
- Last attempted fetch
- HTTP status
- Error message
- Article count
- Created date
- Updated date

### 7.1 Source Types

Source types must be configurable. Suggested starting set:

News · Government · Technology · Business · Local · Weather · Industry · Research · Blog · Magazine

### 7.2 Source Classification and Context

**Do not create a simplistic source "truth score."** An automated system cannot assign a precise truth value to an entire publication, and pretending otherwise damages the product's credibility.

Instead, preserve useful source context:

- Established publication
- Government source
- Local source
- Industry source
- Research source
- Primary source
- Secondary reporting
- Opinion
- Analysis

Where appropriate, the interface communicates the *breadth* of reporting — how many independent sources, of what kinds — rather than a verdict on truth.

## 8. Tags

Create a central tag management system. Tags are reusable across sources, articles, stories, and feed instances.

Each tag supports:

- Tag name
- Slug
- Description
- Parent tag
- Active/inactive status

Hierarchical tags are supported where useful:

```
Energy
├── Power Grid
├── Utilities
├── Nuclear
├── Solar
├── Oil
└── Gas
```

The AI may suggest tags. Administrators must be able to approve, remove, or change them.

## 9. Articles

An article record holds the immutable original data (Layer 1), the system's analysis (Layer 2), and pointers to generated presentation content (Layer 3). At minimum:

- Source reference
- Original headline
- Original description
- Original URL and canonical URL
- Normalized URL
- RSS GUID
- Original publication date
- Author (where available)
- Image (where available)
- Content hash and normalized title (for deduplication)
- Tags
- Extracted entities
- Story assignment
- Importance score
- Duplicate status
- Visibility / editorial state

The system must not reproduce full articles. Store only the metadata and AI-generated summaries the application needs.

## 10. Stories

A story record holds:

- Canonical story identity
- AI-generated headline
- AI-generated summary
- Member articles (via `story_articles`)
- Number of sources
- Source list
- First reported time
- Most recent update
- Tags
- Trending score
- Importance score
- Lifecycle state (see §21)
- Timeline events (see §22)
- Conflicting claims (see §24)
- Related stories
- Editorial flags: featured, verified, locked, hidden

---

# Part III — Collection and Processing

## 11. Article Collection

The system periodically retrieves RSS and Atom feeds. For every item:

1. Retrieve the feed item.
2. Normalize the data.
3. Identify the canonical URL.
4. Extract title.
5. Extract description.
6. Extract publication date.
7. Extract author where available.
8. Extract image where available.
9. Extract source information.
10. Detect duplicates.
11. Assign tags.
12. Determine whether the article belongs to an existing story.
13. Create a new story when appropriate.
14. Generate AI content when enabled.
15. Calculate relevance and importance.
16. Publish the article to matching feed instances.

All of this happens in background jobs. **No expensive processing occurs during normal page requests.**

## 12. Fetching Behavior and Source Politeness

The ingestion system must support:

- Per-source fetch intervals
- Request timeouts
- Retry policies
- Exponential backoff
- Conditional requests (ETag / If-Modified-Since) where supported
- HTTP caching headers where supported
- Maximum items per fetch
- Appropriate user-agent identification
- Temporary source disablement after repeated failures

Being a good citizen toward the publications we depend on is a product requirement, not an optimization.

## 13. Duplicate Detection

Prevent the same article from appearing multiple times. Use multiple signals:

- RSS GUID
- Canonical URL
- Normalized URL
- Content hash
- Normalized title
- Publication date
- Source identifier

The system must tolerate tracking parameters and other incidental URL differences. For example:

```
article.com/story?id=123&utm_source=rss
article.com/story?id=123
```

should normally be treated as the same article.

## 14. Processing Queue

Use background processing throughout. Recommended pipeline:

```
Fetch → Normalize → Deduplicate → Extract metadata → Tag
      → Find related story → Generate AI content → Score → Publish
```

Requirements:

- Each stage must be independently retryable.
- A failure in AI processing must not require the RSS source to be fetched again.
- Failed stages must be visible to administrators and individually re-runnable.

---

# Part IV — Intelligence Layer

## 15. AI Reliability Rules

These rules are non-negotiable and apply to every AI operation in the system.

The AI must:

- Never invent facts.
- Never invent quotations.
- Never invent statistics.
- Never invent sources.
- Never invent events.
- Never change the meaning of a source.
- Never present speculation as established fact.
- Never merge unrelated events.
- Preserve attribution.
- Identify conflicting reports.
- Prefer multiple independent sources.
- Clearly distinguish AI-generated text from original reporting.

The source articles used to generate every synthesized story must be stored.

## 16. AI Provider Abstraction

Implement an AI service layer so the application never depends directly on one provider.

The application requests operations:

- Generate headline
- Generate summary
- Classify article
- Extract entities
- Create embeddings
- Cluster stories
- Synthesize multi-source story
- Identify conflicts

The service layer selects the configured provider and model per operation. This allows different models for different tasks and allows providers to change without redesigning the application.

## 17. AI Headline Generation

When enabled, generate a new headline for each article. The generated headline must:

- Be concise.
- Clearly describe the actual story.
- Improve clarity.
- Avoid clickbait unless specifically configured.
- Never invent information.
- Never exaggerate.
- Preserve the meaning of the original article.

Store both the **original headline** and the **AI headline**. The original must remain available everywhere.

## 18. AI Summary Generation

Generate a short summary for every processed article. The summary must:

- Explain what happened.
- Include important facts.
- Be easy to scan.
- Avoid unnecessary filler.
- Not reproduce the original article.
- Not introduce information absent from the source.

Summary lengths are configurable:

| Setting | Length |
| --- | --- |
| Short | 1–2 sentences |
| Standard | 2–4 sentences |
| Detailed | 1–2 paragraphs |

## 19. Story Detection and Clustering

Create an AI-assisted story clustering system. Articles are evaluated for whether they describe the same underlying event, using signals including:

- Named entities
- Locations
- Organizations
- People
- Dates
- Events
- Keywords
- Semantic similarity
- Publication timing

**Do not merge articles merely because they share general keywords.**

Administrators must be able to manually:

- Merge stories
- Split stories
- Move an article to another story
- Create a new story
- Lock a story from automatic clustering

## 20. Multi-Source Story Synthesis

When multiple independent sources report the same story, the AI creates a synthesized story presentation containing:

- AI-generated headline
- AI-generated summary
- Number of sources
- Source list
- Original article links
- First reported time
- Most recent update
- Tags
- Trending score
- Importance score

The AI must identify differences between reports. For example:

> "Three sources report that the outage affected approximately 50,000 customers. One local source reports a higher estimate."

**Conflicting information must never be silently resolved.**

## 21. Story Lifecycle

Stories carry a lifecycle state:

- Emerging
- Developing
- Active
- Stable
- Cooling
- Archived
- Manually locked
- Verified

State is determined automatically where practical and must remain manually overrideable. A developing story transitions as coverage changes.

## 22. Story Timeline

Each story maintains a chronological timeline of significant developments.

```
8:12 AM   First report
8:27 AM   Local authorities confirm event
8:41 AM   Additional source reports impact
9:03 AM   Official statement released
9:18 AM   Estimated impact revised
```

Every timeline entry must identify the source article or source information supporting the update.

## 23. Importance Scoring

Each article and story carries an importance score used for filtering, feed thresholds, and cost control. Importance is stored alongside its contributing factors so it can be explained (see §34).

## 24. Claim and Conflict Detection

When credible reports disagree about an important fact, the system exposes the disagreement rather than silently selecting one version.

```
Conflicting information
  Source A: 50,000 affected          Source B: 65,000 affected
  Source A: equipment failure        Source B: cause under investigation
```

The system continues tracking conflicting claims until a later authoritative source resolves them.

## 25. Trending Detection

Create a trending score considering:

- Number of independent sources
- Rate of new coverage
- Recency
- Source priority
- Article importance
- Number of related articles
- Sudden increase in coverage
- User engagement
- Historical coverage baseline

Behavior requirements:

- A story that suddenly receives many articles should rise rapidly.
- A topic that normally receives dozens of articles must not automatically be treated as breaking news. Trending is measured against the topic's own baseline.

**Store the individual scoring factors** so administrators can understand why a story is trending, and expose them through the administrative interface (see §34).

## 26. AI Content Versioning

AI-generated headlines, summaries, and synthesized stories must be versioned. Store where applicable:

- Generated content
- AI provider
- Model
- Prompt or prompt version
- Generation timestamp
- Source/article version
- Human modifications
- Current approved version

Original source information must never be overwritten. Regeneration creates a new version; it does not destroy the previous one.

## 27. AI Confidence and the Review Queue

### 27.1 Confidence

AI decisions should carry confidence information where technically practical:

- Story match confidence
- Tag confidence
- Entity confidence
- Headline confidence
- Summary confidence

```
Story Match: 92%
Tags:  Power Grid 98%  ·  Infrastructure 91%  ·  Energy 72%
```

Confidence is an **automation control**, used to decide what runs unattended:

| Confidence | Behavior |
| --- | --- |
| High | Automated |
| Medium | Configurable — automate or route to review |
| Low | Routed to the administrative review queue |

Confidence is an aid to workflow. It must never be presented as a guarantee of factual correctness.

### 27.2 Review Queue

Provide an administrative queue for AI decisions requiring human review:

- Questionable story merges
- Questionable story splits
- Conflicting reports
- Low-confidence tags
- Low-confidence classifications
- Unusual synthesized content

Administrators must be able to resolve these from a single interface without opening each article separately.

## 28. AI Cost Controls

Track where available:

- Request count
- Input size
- Output size
- Estimated cost
- Cost by source
- Cost by feed
- Cost by story
- Daily cost
- Monthly cost
- Projected monthly cost

Provide configurable controls:

- Process only important articles
- Do not regenerate unchanged content
- Cache reusable results
- Use lower-cost models for classification
- Use higher-capability models only for complex synthesis
- Daily AI limits
- Monthly AI limits

---

# Part V — Feeds, Distribution, and Discovery

## 29. Feed Instances

Administrators can create unlimited feed instances. A feed instance is defined from one or more tags, sources, source types, categories, inclusion rules, and exclusion rules.

Each feed instance has:

**Identity**

- Name
- Slug
- Description
- Image/logo
- Active/inactive status

**Composition**

- Included tags
- Excluded tags
- Included sources
- Excluded sources
- Source types
- Categories
- Inclusion rules
- Exclusion rules
- Importance threshold
- Trending threshold
- Maximum articles
- Sort order

**AI settings** (per feed — see §29.2)

Example:

```
Power Grid News
  Includes:  Power Grid · Utilities · Infrastructure · Energy
  Excludes:  Product Reviews · Opinion
```

### 29.1 Visual Feed Builder

Feed instances are configured through a visual builder, not by writing rules blind.

The builder must provide a **live preview**: as the administrator adjusts composition, the interface immediately shows the approximate number of matching stories and a sample of the resulting feed.

Administrators should be able to save a configuration only after seeing what it produces.

### 29.2 Per-Feed AI Settings

Each feed instance controls:

- AI headlines
- AI summaries
- Story clustering
- Multi-source synthesis
- Trending detection
- Minimum importance score
- Maximum article frequency
- Summary length
- Headline style
- Required tags
- Excluded tags

This allows one source database to power completely different publications.

### 29.3 Feed Health and Preview

Each feed instance has an administrative health and preview screen displaying:

- Current story count
- New stories in the last 24 hours
- Number of sources
- Included tags
- Excluded tags
- Recent processing activity
- Recent errors

The actual resulting feed is displayed alongside the configuration, so a misconfigured feed is obvious immediately.

## 30. RSS Output

Every feed instance can optionally generate its own RSS feed, e.g. `/rss/power-grid`.

Generated feeds contain:

- AI headline
- Summary
- Original source
- Original article URL
- Publication date
- Tags
- Featured image where available

The platform consumes RSS and produces its own customized RSS. Generated feeds are cached (see §32).

## 31. Search

Implement search across articles, stories, headlines, summaries, sources, and tags.

Support filtering by:

- Date
- Source
- Tag
- Category
- Story
- Importance
- Trending status

Design the data layer so **semantic search can be added later** without restructuring — embeddings are already a first-class AI operation (§16).

## 32. Performance and Caching

The public interface must never wait for AI processing. All expensive work is asynchronous.

Cache:

- Feed data
- AI results
- Story clusters
- Trending scores
- Generated RSS feeds

Additional requirements:

- Pagination or infinite scrolling for large feeds
- Lazy loading for images
- Page response time monitored as an operational metric (§45)

## 33. Future Output Channels

The core article/story database remains independent of the presentation layer. Planned and potential channels are listed in §4.3. Adding one must require no changes to ingestion, clustering, or scoring.

---

# Part VI — Interfaces

## 34. Reader Experience

### 34.1 Principles

The public interface is visually clean and focused on reading. The reader should understand what happened, why it matters, and where the information came from **without opening several pages** — and without being forced to read several versions of the same event merely because multiple publications reported it.

Stories are the primary unit. Individual source articles remain available underneath each story as the underlying reporting.

### 34.2 Main Page

**Trending** — large story cards showing:

- AI headline
- Summary
- Tags
- Time
- Number of sources
- Featured image
- Link to story

**Latest** — chronological article and story stream.

**Topics** — browse by tag.

**Sources** — browse by publication.

Avoid excessive visual clutter.

### 34.3 Story Page

A story page displays:

- AI-generated headline
- AI-generated summary — what happened
- Publication/update time
- Relevant tags
- Source count and source list
- Source articles
- Timeline of major updates (§22)
- Conflicting information where applicable (§24)
- Related stories
- Original source links

Where appropriate, show contextual framing:

- "First reported by…"
- "Additional coverage…"
- "Sources disagree on…"

This gives the reader context without pretending the AI is the source of the information.

### 34.4 Minimal Reading Mode

Provide a focused presentation for readers who want the essentials quickly, emphasizing:

- Headline
- What happened
- What changed
- Sources
- Timeline
- Conflicting information
- Original reporting

The full story view remains available.

### 34.5 AI Attribution in Public

AI-generated material must be clearly identified.

```
AI Summary
Generated from 6 source reports.

Original reporting
Reuters · AP · Local News · Government
```

The distinction must make clear that the AI is organizing and summarizing reporting, not becoming the source of it.

### 34.6 Original Source Attribution

Every article retains its original source. Display:

- Publication name
- Original headline
- Publication date
- Original URL

All links point to the original publication. The system does not reproduce full articles; it stores only the metadata and AI-generated summaries the application requires.

### 34.7 Reader Preferences

The architecture must allow future reader preferences:

- Favorite topics
- Hidden topics
- Favorite sources
- Reading history
- Custom feeds
- Sort preferences
- Compact reading view
- Comfortable reading view
- Light/dark mode

These must not alter the canonical article or story records.

## 35. Administrative Experience

### 35.1 Task-Oriented Dashboard

The dashboard prioritizes what needs attention, then reports activity, then offers operations detail.

**Needs Attention** (top)

- Failed sources
- AI decisions awaiting review
- Stories with conflicting reports
- Processing problems
- Feeds producing unexpected results

**Today's Activity**

- Articles collected
- Articles processed
- Stories created
- Trending stories
- AI processing activity

**Operational Statistics**

- Articles collected today
- Articles processed today
- Active sources
- Failed sources
- Processing queue depth
- Trending stories
- New stories
- AI processing failures
- Feed instance activity
- Recent errors

**Quick Actions**

- Add source
- Create feed
- Manage tags
- Review stories
- Review failed processing

### 35.2 Source Onboarding Wizard

Adding a source is a guided workflow, not a blank form.

The administrator enters either a website URL or an RSS/Atom feed URL. When a website URL is entered, the system attempts to discover available RSS/Atom feeds.

```
URL → Detect → Preview → Configure → Test → Activate
```

The preview shows:

- Source name
- Website URL
- Detected RSS/Atom feed URL
- Logo/image
- Description
- Suggested source type
- Suggested tags
- Feed validity
- Latest articles
- Publication frequency where detectable
- Last publication
- Potential duplicate or configuration issues

The administrator approves the detected information before activation.

### 35.3 Source Health Monitoring

Monitor every RSS source and track:

- Last successful request
- Last failed request
- HTTP status
- Response time
- Number of items returned
- Parsing errors
- Consecutive failures

Broken sources must be clearly identified, and administrators must be able to test a source manually at any time. This operates alongside the fetch politeness controls in §12.

### 35.4 Editorial Controls

Administrators must be able to:

- Hide articles
- Hide stories
- Hide sources
- Edit headlines
- Edit summaries
- Change tags
- Merge stories
- Split stories
- Feature stories
- Change importance
- Mark stories as verified
- Prevent a story from being automatically modified
- Reprocess an article
- Reprocess a story
- Disable AI processing for individual content

**Manual changes override automated changes.** A human decision must not be silently undone by the next processing run.

### 35.5 "Why Am I Seeing This?"

Administrators must be able to see why an article or story was included in a feed, and why it received a particular score.

```
Why this story is here:
  Matches: Power Grid        Source: Reuters
  Importance: 82             Trending: 91
  Related articles: 7        Published: 18 minutes ago

Why this is trending:
  8 independent sources
  14 articles in 45 minutes
  Historical hourly coverage: 2 articles
  Coverage increasing rapidly
```

The individually stored scoring factors (§23, §25) are the data behind this view.

### 35.6 Global Search and Command Navigation

Provide a global search/command field in the administrative interface that searches or navigates to sources, articles, stories, tags, feed instances, users, and settings.

Searching "power grid" might return: the Power Grid News feed, the Power Grid tag, related stories, related sources, failed sources, and relevant articles.

### 35.7 Saved Administrative Views

Administrators can save frequently used filters, available from the administrative navigation. Examples:

- Failed Sources
- Stories Needing Review
- Trending Stories
- AI Confidence Below 70%
- Articles Published Today
- Unassigned Articles
- Stories With Conflicts

## 36. Change History and Undo

Manual editorial changes must be reversible. For important changes, maintain:

- Previous value
- New value
- User
- Timestamp
- Reason where applicable

Tracked actions include: story merge, story split, story reassignment, tag changes, headline changes, summary changes, source changes, hidden/visible status, importance changes, and verification changes.

Provide undo where safe and practical.

## 37. Audit Log

Maintain a dedicated audit log, separate from technical system logs, that makes administrative changes traceable in human terms.

```
John changed "Power Grid News"
  Added tag: Infrastructure
  Removed source: Example Source
  7:42 AM
```

## 38. Roles and Permissions

The `users` entity supports role-based access. Suggested roles:

- Owner
- Administrator
- Editor
- Reviewer
- Read-only

Permissions control access to:

- Source management
- AI configuration
- Story editing
- Feed creation
- User management
- System settings
- Editorial controls

## 39. Notifications

The system notifies administrators when:

- A source fails repeatedly
- Processing stops
- The AI provider fails
- The queue becomes too large
- A feed produces zero results
- An unusually large story develops
- An important story requires review

Channels may include email, Discord, Slack, and webhooks. Notification delivery must remain independent of the core article/story data model.

## 40. Design

Use a modern, restrained news interface.

Prioritize:

- Readability
- Strong typography
- Clear hierarchy
- Generous spacing
- Fast loading
- Consistent cards
- Simple navigation
- Clear source attribution

Avoid excessive gradients, animations, giant buttons, unnecessary popups, and dashboard clutter.

The interface should feel like a serious information product rather than an AI demo. Provide light and dark modes where practical, and make the interface responsive on desktop, tablet, and mobile.

---

# Part VII — Platform and Operations

## 41. Source Control

GitHub is the primary source-control and development platform, used for:

- Source control
- Pull requests
- Code review
- Issue tracking
- Documentation
- Database migrations
- Automated tests
- CI/CD
- Release tags
- Change history

Branch structure:

| Branch | Purpose |
| --- | --- |
| `main` | Production |
| `staging` | Staging |
| Feature branches | Development work |

Changes move through pull requests rather than being committed directly to production.

## 42. Continuous Integration

Use GitHub Actions for automated checks and deployment workflows. CI must run at minimum:

- Syntax checks
- Unit tests
- Integration tests
- Database tests where practical
- API tests
- Front-end tests
- Build validation
- Security/dependency checks

Successful changes are then deployed through the Railway workflow.

## 43. Runtime Architecture

Railway is the primary deployment and runtime platform. The deployment is designed as independent services.

```
GitHub → GitHub Actions → Railway
                            ├── Web Application
                            ├── API
                            ├── Worker
                            ├── Scheduler
                            ├── PostgreSQL
                            └── Redis
```

The exact service breakdown may be adjusted during implementation, but **the web application must not perform long-running ingestion or AI processing during normal page requests.**

### 43.1 Background Workers

The worker service handles expensive asynchronous operations:

- RSS fetching
- Article normalization
- Duplicate detection
- Metadata extraction
- Tagging
- Story clustering
- AI processing
- Trending calculations
- RSS generation
- Retry operations

The public application remains responsive regardless of processing activity.

### 43.2 Scheduler

The scheduler determines which sources need fetching and enqueues the appropriate jobs. It does not perform the processing itself.

### 43.3 PostgreSQL

PostgreSQL stores the canonical application data: sources, articles, stories, tags, feed instances, users, jobs, AI results, source fetch logs, system logs, audit logs, and settings.

Database migrations live in GitHub and are applied through controlled deployment processes.

### 43.4 Redis

Redis may be used for background job queues, caching, temporary processing state, and rate limiting where appropriate. **Canonical article/story data remains in PostgreSQL.**

## 44. Environments

| Environment | Purpose |
| --- | --- |
| Development | Local development environment |
| Staging | Railway staging environment for testing changes before production |
| Production | Railway production environment serving the live application |

Promotion flow:

```
Developer → Git branch → Pull Request → GitHub Actions → Automated Tests
         → Merge → Railway Staging → Verification → Production
```

**Production secrets and credentials must never be stored in source control.**

## 45. Observability

The system provides operational visibility beyond basic logs. Track:

- Job duration
- Queue depth
- Failed jobs
- Retry count
- API failures
- AI latency
- Database latency
- Feed generation time
- Page response time
- Error rate
- Service health

Provide an administrative system-health view that surfaces these metrics, and wire the notification triggers in §39 to them.

## 46. Import, Export, Backup, and Recovery

### 46.1 Import and Export

Provide administrative **export** for:

- Sources
- Tags
- Feed configurations
- Articles
- Stories

Support **import** where appropriate for:

- Sources
- Tags
- Feed configurations

### 46.2 Backup and Recovery

Define a backup and recovery strategy for PostgreSQL and other critical configuration. The plan must define:

- Backup frequency
- Retention
- Backup verification
- Restore procedure
- Configuration recovery
- Secret recovery
- Disaster recovery procedure

**Backups must be tested by performing actual restores.** The existence of a backup is not evidence that it works.

---

# Part VIII — Delivery

## 47. Build Sequence

Build in vertical, testable stages. **At every stage the system must remain functional and usable.** Do not build the entire interface before the underlying article and story model has been validated.

### Phase 1 — Foundation

1. Architecture and repository setup
2. GitHub repository and CI
3. Railway development and staging environments
4. Database schema and migrations
5. Authentication and permissions

### Phase 2 — Ingestion

6. Source management
7. RSS/Atom ingestion
8. Processing queue
9. Article normalization
10. Deduplication
11. Tagging

### Phase 3 — First Usable Product

12. Basic article/story API
13. Basic public interface
14. Feed builder
15. Feed preview

### Phase 4 — Intelligence

16. AI abstraction layer
17. AI headlines
18. AI summaries
19. Story clustering
20. Multi-source synthesis
21. Conflict detection
22. Trending engine
23. Administrative review queue

### Phase 5 — Discovery and Distribution

24. Search
25. RSS output

### Phase 6 — Operations

26. Source health
27. Administrative dashboard
28. Monitoring and observability
29. Notifications

### Phase 7 — Polish and Hardening

30. Performance optimization
31. Responsive/mobile UI
32. Security review
33. Automated testing and error handling
34. Production deployment
35. Backup and restore testing

## 48. Testing Strategy

Testing occurs throughout development, not at the end. Include:

- Unit tests
- Database tests
- RSS parser tests
- Normalization tests
- Duplicate detection tests
- Story clustering tests
- AI output validation
- API tests
- Authentication tests
- Feed generation tests
- End-to-end browser tests
- Performance tests
- Failure/retry tests

### 48.1 The Permanent Test Dataset

Maintain a permanent test dataset containing known articles, duplicates, related articles, unrelated articles, conflicting reports, and developing stories.

**Changes to clustering, scoring, and AI processing must be evaluated against this dataset before production deployment.** This dataset is the only reliable defense against silent regressions in behavior that is otherwise difficult to observe.

## 49. Acceptance Criteria

The finished application allows an administrator to:

**Sources**

- Add an RSS source.
- Assign tags to the source.
- Automatically retrieve articles.
- Detect broken RSS sources.
- Reprocess failed articles.

**Articles and stories**

- Detect duplicate articles.
- Automatically tag articles.
- Generate improved headlines.
- Generate summaries.
- Detect articles covering the same story.
- Combine multiple sources into one story.
- Identify rapidly developing stories.
- See original sources for every piece of information.
- Review and override AI decisions.

**Feeds**

- Create a custom feed from tags.
- Preview the resulting feed.
- Publish the feed.
- Generate RSS output from the feed.

**Overall**

- Manage everything from a clean administrative interface.

And a reader can:

- Land on the site and immediately understand what is happening.
- Open a story and understand the event without reading several source articles.
- See how many and which sources reported it.
- See where sources disagree.
- Reach the original reporting in one click.

The final result must feel like a curated, intelligent news platform rather than an RSS feed directory — and must feel simple to operate despite the complexity underneath it.

---

# Appendices

## Appendix A — Worked Example: The Power Outage

A single example traced through the system, useful as an implementation reference and as a demo script.

| Stage | What happens |
| --- | --- |
| Fetch | Reuters, AP, a local newspaper, and an industry trade publication each publish an item about a regional power outage. |
| Normalize | Each item is normalized; canonical URLs identified; tracking parameters stripped. |
| Deduplicate | The AP item appears in two feeds the platform subscribes to; the second copy is recognized as a duplicate by GUID and canonical URL. |
| Tag | Articles receive Power Grid (98%), Infrastructure (91%), Energy (72%). |
| Cluster | All four articles are matched to one story at 92% confidence. |
| Synthesize | An AI headline and summary are generated from all four reports, with sources stored. |
| Conflict | Three sources say ~50,000 customers affected; the local paper says 65,000. Both claims are recorded and surfaced. |
| Score | 8 independent sources, 14 articles in 45 minutes against a baseline of 2/hour → high trending score. |
| Publish | The story enters the Power Grid News feed and its RSS output at `/rss/power-grid`. |
| Read | A reader sees one story card, opens it, reads the summary, sees the timeline, sees the disagreement about impact, and clicks through to Reuters. |

## Appendix B — Non-Negotiables

The short list. If a design decision conflicts with one of these, the design decision is wrong.

1. Original source data is never overwritten by AI or system-derived content.
2. Every piece of displayed information traces back to an original source with a working link.
3. AI-generated text is always visually distinguishable from original reporting.
4. Conflicting reports are surfaced, never silently resolved.
5. Manual editorial decisions override automated ones.
6. Full article text is never reproduced.
7. No expensive processing happens on the public request path.
8. Every pipeline stage is independently retryable.
9. Every output channel consumes the same canonical story services.
10. Production secrets never enter source control.

## Appendix C — Section Mapping from Prior Versions

| Prior section (v1.01 / 1.02 / 1.03) | Location in this document |
| --- | --- |
| 1. Core Concept | §3 |
| 2. Source Management | §7, §35.2 |
| 3. Tags | §8 |
| 4. Article Collection | §11 |
| 5. Duplicate Detection | §13 |
| 6. Story Detection | §19 |
| 7. AI Headline Generation | §17 |
| 8. AI Summary Generation | §18 |
| 9. Multi-Source Story Synthesis | §20 |
| 10. Trending Detection | §25 |
| 11. Feed Instances | §29 |
| 12. AI Settings Per Feed | §29.2 |
| 13. Public Interface | §34.1, §34.2 |
| 14. Story Page | §34.3 |
| 15. Original Source Attribution | §34.6 |
| 16. Search | §31 |
| 17. RSS Output | §30 |
| 18. Admin Dashboard | §35.1 |
| 19. Source Health | §35.3 |
| 20. Processing Queue | §14 |
| 21. AI Reliability Rules | §15 |
| 22. Editorial Controls | §35.4 |
| 23. Performance | §32 |
| 24. Data Model | §6 |
| 25. Design | §40 |
| 26. Future Architecture | §4.3, §33 |
| 27. Important Product Principle | §2 |
| 28. Build Order | §47 |
| 29. Acceptance Criteria | §49 |
| 30.1 Product Experience Principle | §5 |
| 30.2 Source Onboarding Wizard | §35.2 |
| 30.3 Visual Feed Builder | §29.1 |
| 30.4 Story-Centered Public Experience | §34.1, §34.3 |
| 30.5 Story Lifecycle | §21 |
| 30.6 Story Timeline | §22 |
| 30.7 Source Classification and Context | §7.2 |
| 30.8 Claim and Conflict Detection | §24 |
| 30.9 AI Confidence | §27.1 |
| 30.10 AI Review Queue | §27.2 |
| 30.11 "Why Am I Seeing This?" | §35.5 |
| 30.12 Task-Oriented Admin Dashboard | §35.1 |
| 30.13 Global Search and Command Navigation | §35.6 |
| 30.14 Change History and Undo | §36 |
| 30.15 AI Content Versioning | §26 |
| 30.16 AI Provider Abstraction | §16 |
| 30.17 AI Cost Controls | §28 |
| 30.18 RSS Fetching and Source Politeness | §12 |
| 30.19 Observability | §45 |
| 30.20 Authentication and Authorization | §38 |
| 30.21 Audit Log | §37 |
| 30.22 Notifications | §39 |
| 30.23 Feed Health and Preview | §29.3 |
| 30.24 Saved Administrative Views | §35.7 |
| 30.25 Reader Preferences | §34.7 |
| 30.26 Minimal Story Reading Mode | §34.4 |
| 30.27 AI Attribution in the Public Interface | §34.5 |
| 31.1–31.2 GitHub / Actions | §41, §42 |
| 31.3–31.5 Railway / Workers / Scheduler | §43 |
| 31.6–31.7 PostgreSQL / Redis | §43.3, §43.4 |
| 31.8 Environment Separation | §44 |
| 32.1 Canonical Article and Story Model | §4.1 |
| 32.2 Separation of Data Layers | §4.2 |
| 32.3 Output Independence | §4.3 |
| 33. Testing Strategy | §48 |
| 34. Import, Export, Backup, Recovery | §46 |
| 35. Revised Development Approach | §47 |
| 36. Final Product Principle | §2, §49 |

## Appendix D — Consolidation Notes

- Versions 1.01 and 1.03 are byte-identical. Version 1.02 is the base specification (sections 1–29) without the later addenda. This document merges all of them.
- The two build orders (old §28 and §35) were reconciled into one sequence (§47). The revised order was used as the spine because it accounts for infrastructure, authentication, and the AI abstraction layer; nothing from the original order was dropped.
- The product principle appeared twice (old §27 and §36). The fuller phrasing from §36 is used, stated once at §2 and echoed only in the acceptance criteria.
- Feed configuration was described in four places (old §11, §12, §30.3, §30.23). These are now one section (§29) with subsections.
- The story page was described in old §14, §30.4, and §30.6. These are now §34.3 with the timeline at §22.
- Data-model requirements from old §24, §31.6, and the entities implied by the addenda (audit logs, change history, confidence, claims, cost records, saved views, reader preferences) are consolidated at §6.
- Requirement language was normalized to must/should/may, and cross-references were added so no requirement needs to be restated to stay findable.

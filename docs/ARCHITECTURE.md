# AI Content Manager SaaS --- Architecture

**Status:** Proposed baseline \| **Last updated:** 2026-10-24

## 1. Purpose

A multi-tenant SaaS workspace for AI/SaaS businesses to research
audiences, plan campaigns, generate platform-specific content, review
and approve it, schedule/publish through supported official APIs, and
learn from analytics.

### Goals

-   Support LinkedIn, Instagram, Facebook, YouTube, X, and TikTok where
    official API access and permissions allow.
-   Provide a complete local workflow using a mock social adapter.
-   Enforce explicit human approval before every external publish
    action.
-   Isolate organization data and audit important actions.
-   Keep AI and social providers replaceable through adapters.

### Initial non-goals

-   Unapproved autonomous publishing.
-   Scraping or API access that bypasses platform controls.
-   Guaranteed feature parity across platforms.
-   Fake engagement, spam, or guaranteed audience-growth claims.

## 2. Architecture principles

1.  Server-side authorization is authoritative; UI checks are only UX.
2.  Every tenant-owned record is scoped to an organization.
3.  AI output is editable, validated, versioned, and traceable.
4.  Publishing is asynchronous, durable, idempotent, and reconciled
    after ambiguous outcomes.
5.  Use official APIs and capability checks; do not assume all accounts
    have the same permissions.
6.  Local development must work without real platform credentials.
7.  Secrets never enter source control, browser bundles, logs, or
    analytics.
8.  Retrieved pages, uploads, comments, and AI outputs are untrusted
    input.
9.  Build vertical slices with tests instead of disconnected screens.
10. Production readiness includes monitoring, backup/restore, privacy
    workflows, and security review.

## 3. Logical architecture

``` mermaid
flowchart LR
  U[Brand users] --> WEB[Next.js dashboard]
  WEB --> API[FastAPI API]
  API --> DB[(PostgreSQL)]
  API --> R[(Redis / queue)]
  API --> OBJ[(Private object storage)]
  R --> W[Background workers]
  W --> LLM[LLM provider adapter]
  W --> SA[Social platform adapters]
  SA --> SP[LinkedIn / Meta / YouTube / X / TikTok APIs]
  W --> AN[Analytics ingestion]
  AN --> SP
```

## 4. Recommended stack

  -----------------------------------------------------------------------
  Layer                   Default                 Responsibility
  ----------------------- ----------------------- -----------------------
  Web                     Next.js App Router,     Dashboard and workflows
                          React, TypeScript       

  Styling                 Tailwind CSS and        Design system in
                          reusable accessible     `DESIGN.md`
                          components              

  API                     FastAPI, Python 3.12,   Versioned REST API
                          Pydantic v2             

  Data access             SQLAlchemy 2, Alembic   ORM and migrations

  Database                PostgreSQL              Durable tenant data

  Queue/cache             Redis plus Celery or    Async work and
                          maintained equivalent   short-lived cache

  Storage                 S3-compatible private   Media and documents
                          object storage          

  AI                      Provider-neutral LLM    Generation, research
                          interface               summaries, reviews

  Auth                    Maintained OIDC/auth    Sessions and identity
                          library or identity     
                          provider                

  Local runtime           Docker Compose          PostgreSQL and Redis;
                                                  optional MinIO
  -----------------------------------------------------------------------

Pin dependencies at implementation time and use maintained secure
releases. Do not treat this suggested stack as a reason to replace
working technology already present in a repository without assessing it
first.

## 5. Service boundaries

### Web app

Authentication UX, onboarding, dashboard, brand knowledge, personas,
research, campaigns, editor, platform previews, media library, calendar,
Kanban, approvals, publishing history, community inbox, analytics, agent
activity, team/roles, integrations, usage, billing, and audit log.

### API/domain services

Identity and memberships; RBAC; brand profile and documents; personas;
research records; campaigns; content items and immutable versions;
media; quality reviews; approvals; social accounts and capabilities;
publish jobs; published posts; analytics snapshots; agent runs; usage;
subscriptions; audit logs; webhooks and outbox events.

### Workers

Research retrieval from permitted sources, generation, media processing,
quality review, scheduled publishing, publish reconciliation, analytics
ingestion, notifications, and webhook delivery. A queued job is not
proof of authorization: workers must revalidate permissions and business
rules.

## 6. Specialist agents

  -----------------------------------------------------------------------
  Agent                   Responsibility          Output
  ----------------------- ----------------------- -----------------------
  Market Intelligence     Summarize permitted     Source-linked findings
                          audience/market sources and confidence

  Content Strategist      Convert goals and       Audience, message
                          research to campaign    pillars, CTA, KPI,
                          briefs                  cadence

  Cross-Platform          Draft channel-specific  Structured editable
  Copywriter              variants                drafts

  Creative Director       Propose creative        Concepts, prompts,
                          concepts and media      storyboard
                          briefs                  

  Quality / Brand Safety  Check claims, tone,     Findings and
  Reviewer                policy, and platform    pass/revise/reject
                          fit                     recommendation

  Publishing Coordinator  Prepare schedules and   Job request only; never
                          publish jobs            bypasses approval

  Community Manager       Classify supported      Suggested response
                          interactions and draft  drafts
                          replies                 

  Analytics / Growth      Explain performance and Metric summaries and
  Analyst                 propose experiments     test hypotheses
  -----------------------------------------------------------------------

Record agent name/version, organization, actor, inputs and
content-version references, model/provider, status, timestamps,
validated output, source references, errors, and usage/cost when
available. Show concise rationales and evidence, not hidden
chain-of-thought.

## 7. Content lifecycle and approval invariant

``` mermaid
stateDiagram-v2
  [*] --> Draft
  Draft --> Generating
  Generating --> InReview
  InReview --> Draft: Changes requested
  InReview --> ReadyForApproval: Checks pass
  ReadyForApproval --> Approved: Exact version approved
  ReadyForApproval --> Draft: Rejected
  Approved --> Scheduled
  Approved --> Publishing
  Scheduled --> Publishing: Due and revalidated
  Publishing --> Published: Provider confirms
  Publishing --> PublishFailed: Error or uncertain outcome
  PublishFailed --> Scheduled: Safe retry after reconciliation
```

**Mandatory invariant:** no external publish call without server-side
validation that the exact immutable content version, media manifest,
platform account, destination, action, and approval are valid. Recheck
immediately before the provider call. Any relevant edit invalidates
approval. The approval record includes organization, version ID/hash,
account, destination, action, reviewer, decision, time, policy version,
expiry if used, and invalidation reason.

Before publishing, verify tenant ownership, account
ownership/connection, reviewer authorization, approval validity, policy
checks, platform capability, and idempotency status. Use a durable
outbox or equivalent dispatch pattern. If a timeout makes success
ambiguous, reconcile remote status before retrying; never blindly retry
a potentially successful post.

Provide a pause-publishing control, disconnect/revoke controls, safe
cancellation before dispatch, and an auditable operator kill switch. A
pause cannot retract already-published posts.

## 8. Social adapters

Define a common interface for OAuth initiation/callback, token
refresh/health, account identity, capability discovery, payload
validation, publish, status lookup, analytics retrieval, and
disconnect/revoke where supported.

Adapters: - `LinkedInAdapter` - `InstagramAdapter` - `FacebookAdapter` -
`YouTubeAdapter` - `XAdapter` - `TikTokAdapter` - `MockSocialAdapter`

Use official APIs only. Confirm current permissions, app review, account
eligibility, quotas, API tier, and terms before enabling a feature.
Store tokens encrypted at rest with production key management. Never
return provider secrets to the browser. Expose capability status and
missing-permission explanations in the UI.

## 9. Data model

Core tables/entities: - `users`, `organizations`,
`organization_memberships` - `brand_profiles`, `brand_documents`,
`audience_personas` - `research_sources`, `research_items`,
`campaigns` - `content_items`, `content_versions`, `media_assets` -
`quality_reviews`, `approval_records` - `social_accounts`,
`social_account_capabilities` - `publish_jobs`, `published_posts` -
`engagement_events`, `analytics_snapshots` - `agent_runs`,
`usage_events`, optional `subscriptions` - `audit_logs`,
`webhook_deliveries`, `outbox_events`

Tenant-owned rows must have `organization_id` directly or be reachable
through an enforced tenant-scoped parent. Use foreign keys, unique
constraints, indexes, explicit deletion/retention rules, and migrations.
Important constraints include unique membership per organization/user,
unique content version sequence, approval tied to immutable version and
destination/action, stable publish idempotency key, and deduplication
for webhook deliveries.

## 10. API conventions

-   Base path `/api/v1`; OpenAPI from FastAPI.
-   Pydantic validation for all requests and external/provider outputs.
-   Stable error schema with machine-readable code, safe message, field
    errors, and correlation ID.
-   Cursor pagination for large collections; allowlisted filters/sorts.
-   UTC timestamps in storage and ISO 8601 responses.
-   Idempotency keys for side-effecting operations where appropriate.
-   Rate limits for users, organizations, IPs, and sensitive actions.
-   Signed, timestamp-checked, replay-protected webhooks.
-   Suggested route groups: `/organizations`, `/members`, `/brand`,
    `/personas`, `/research`, `/campaigns`, `/content`, `/reviews`,
    `/approvals`, `/social-accounts`, `/publish-jobs`,
    `/published-posts`, `/inbox`, `/analytics`, `/agent-runs`, `/usage`,
    `/billing`, `/audit-logs`, `/health/live`, `/health/ready`.

## 11. Security and privacy

-   Use maintained authentication; do not implement password
    cryptography yourself.
-   Enforce RBAC and tenant scope on every read and write.
-   Consider PostgreSQL row-level security as defense in depth; set
    tenant context transactionally and test connection-pool reuse.
-   Apply CSRF protections for cookie sessions; use secure, HTTP-only,
    SameSite cookies where appropriate.
-   Validate uploads by size, extension, MIME signature, and safe
    processing; scan for malware when available.
-   Prevent SSRF in URL fetches: block private/link-local/metadata IPs,
    validate redirects/DNS, and enforce egress limits.
-   Sanitize rendered user content and use safe Markdown/HTML rendering.
-   Encrypt tokens/secrets at rest; rotate credentials and keys.
-   Provide retention, export, disconnect, and deletion workflows.
-   Audit approvals, publishing, role changes, OAuth changes, and
    destructive actions.
-   Never log passwords, tokens, cookies, or unredacted sensitive
    payloads.
-   Add dependency, secret, static-analysis, and container scanning in
    CI.

## 12. Reliability and observability

Persist job state before dispatch; use bounded exponential retries with
jitter; classify validation, auth, rate-limit, transient, ambiguous, and
terminal errors. Provide dead-letter status and operator-visible retry
controls. Track queue age, job duration, provider latency/error rates,
token refresh failures, publish reconciliation, and usage. Add
correlation IDs across web/API/worker/provider calls. Distinguish
liveness and readiness. Define backups and test restores.

## 13. Testing

Required coverage: - Domain state transitions and approval policy. - API
validation, RBAC, and tenant isolation / IDOR attempts. - Clean-database
migrations. - Approval invalidation after content or destination
changes. - Concurrent duplicate publish requests and idempotency. -
Worker retries, rate limits, timeouts, ambiguous outcomes, and
reconciliation. - Mock adapter contract tests. - Editor, previews,
calendar, and approval frontend tests. - E2E: onboarding → brand →
campaign → draft → review → approval → mock publish → analytics. -
Accessibility tests for key workflows.

## 14. Delivery phases

1.  **Reliable vertical slice:** auth/membership, tenant isolation,
    brand, campaign, content versions, review/approval, mock adapter,
    durable job, audit, E2E test.
2.  **AI workflow:** LLM adapter, source-linked research, specialist
    agents, structured outputs, quality review, usage tracking.
3.  **Real integrations:** verify and enable platforms individually
    after access/permissions are approved.
4.  **Analytics/community:** supported metrics, freshness, and permitted
    community features.
5.  **Production hardening:** billing if needed, export/deletion,
    monitoring, backup/restore, security review, runbooks.

## 15. Definition of done

A feature requires working domain logic, authorization, migrations,
validation, tests, error states, observability, documentation, and
accessible UX. A publishing feature must prove server-enforced approval,
destination scoping, idempotency, and safe ambiguous-outcome handling.

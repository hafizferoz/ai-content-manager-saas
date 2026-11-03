# Implementation Status

**Current status:** Phase 1 and Phase 2 slices are implemented. Phase 3
includes tested LinkedIn OAuth and a text-only member-post adapter;
live LinkedIn app/account verification and other platforms remain
outstanding. Phase 4 now includes tenant-scoped analytics snapshots,
experiment logging, and a mock/manual community inbox with reviewed
reply drafts; provider analytics and community APIs are not enabled.

**Cross-cutting UX:** Browser-native prompts and confirms have been
replaced with styled application dialogs; browser-native validation
bubbles are intercepted; notifications distinguish success,
information, and errors.

## Repository assessment

-   [x] Inspect current repository and stack (documentation-only starter)
-   [x] Inventory existing features/tests (none were present)
-   [x] Confirm architecture choices (FastAPI, SQLAlchemy/Alembic, Next.js)

## Phase 1 --- Reliable vertical slice

-   [x] Web/API/worker structure and developer commands
-   [x] Docker Compose for PostgreSQL and Redis
-   [x] Authentication and organization membership
-   [x] Server-side tenant isolation and RBAC
-   [x] Brand profile updates and campaign creation/listing
-   [x] Content item and immutable version model
-   [x] Quality review and approval workflow
-   [x] Approval tied to exact version/account/destination/action
-   [x] Mock social adapter and durable publish jobs
-   [x] Idempotency, reconciliation, audit trail
-   [x] API E2E draft → review → approval → mock publish
-   [x] Automated migration and authorization/error-state tests
-   [x] Container/local full-stack execution verified
-   [ ] CI, production observability, backup/restore and security hardening

## Phase 2 --- AI workflow

-   [x] Provider-neutral LLM adapter and validated structured outputs
-   [x] Source-linked research using user-supplied source excerpts (no URL fetching)
-   [x] Persisted campaign strategy history with source/run traceability
-   [x] Market Intelligence agent
-   [x] Content Strategist agent
-   [x] Cross-Platform Copywriter agent
-   [x] Creative Director agent
-   [x] Quality/Brand Safety Reviewer agent
-   [x] Tenant-scoped agent run history, estimated token usage, platform previews
-   [x] AI workflow tests for output validation, source scoping, errors, and usage
-   [x] Mock/provider adapter contract tests (HTTP mocked; no external credentials used)
-   [x] Safe, status-specific provider failure guidance in workflow responses, including quota-versus-rate-limit classification; error codes persisted in agent-run history
-   [x] Versioned five-workflow evaluation corpus and safe-by-default regression runner
-   [ ] Run and review live-provider quality baselines for each supported deployment model

## Phase 3 --- Real social integrations

-   [x] Tenant-scoped capability registry and mock adapter boundary
-   [x] LinkedIn OAuth authorization-code connection with hashed, expiring, single-use state
-   [x] Normalize comma- and whitespace-delimited LinkedIn token scopes and repair legacy saved scopes/capability state
-   [x] Fernet-encrypted LinkedIn access and refresh token persistence
-   [x] LinkedIn text-only member post adapter gated by `w_member_social` and human approval
-   [x] LinkedIn profile identity, account disconnect, and permission-aware capabilities
-   [x] Approval requests use a styled destination dialog that lists workspace accounts, including unavailable/inactive status and the provider capability reason
-   [x] Real publishing defaults off and requires explicit `PUBLISHING_ENABLED=true`
-   [x] LinkedIn HTTP 429 classification with explicit Retry-After wait and operator-authorized retry
-   [x] Owner/admin-only, audited manual reconciliation for uncertain LinkedIn outcomes
-   [ ] Production LinkedIn credential/key rotation, remote revocation, and live app/permission verification
-   [ ] Refresh lifecycle verified against an app approved for LinkedIn refresh tokens
-   [ ] Live LinkedIn callback and post verified against the configured app and account
-   [ ] Verify a live post's continued visibility through an approved LinkedIn read permission; `w_member_social` alone does not provide read access
-   [ ] Instagram integration verified against current requirements
-   [ ] Facebook integration verified against current requirements
-   [ ] YouTube integration verified against current requirements
-   [ ] X integration verified against current requirements
-   [ ] TikTok integration verified against current requirements
-   [ ] Automated rate-limit scheduling, platform-wide rate-limit controls, and provider-backed reconciliation

## Phase 4 --- Analytics/community

-   [x] Tenant-scoped mock/manual analytics snapshots with source, period, capture time, and explicit unavailable metrics
-   [x] Metric definitions that identify units and non-comparable platform-specific rates/reach
-   [x] Date, platform, account, campaign, and content filters plus CSV export
-   [x] Experiment log with hypothesis, variant, period, primary metric, outcome, and next action
-   [x] Tenant-scoped mock/manual community inbox with conversation IDs, assignment, filtering, and resolution
-   [x] Reply draft, submit-for-review, and author-separated approval/change/rejection controls; no external send route
-   [ ] Provider-backed analytics sync and platform-specific community API adapters after permission verification
-   [ ] Saved replies and automated interaction classification

## Phase 5 --- Production readiness

-   [ ] Billing if required
-   [ ] Data export/deletion and retention
-   [ ] Backup/restore runbook and restore test
-   [ ] Monitoring, alerting, correlation IDs
-   [ ] CI tests and security/dependency/container scanning
-   [ ] Security review, load/failure tests, deployment/rollback
    runbooks

## Known limitations

-   LinkedIn authorization and a text-only member-post adapter are implemented but require app-specific credentials, a registered callback URL, token-encryption key, and approved `w_member_social` permission before the real path can be exercised. Other social publishing remains mock-only.
-   `USE_MOCK_SOCIAL_ADAPTER` is not a global adapter switch. The selected account determines mock versus live behavior; the approval UI requires the destination choice and warns when approving a live LinkedIn post.
-   AI uses a deterministic mock by default. A versioned synthetic corpus checks structured outputs, source scoping, and selected brand-safety expectations; provider-specific live quality baselines, budgets, and deployment secrets management are not production-ready.
-   Research stores user-supplied source URLs and excerpts; URL fetching/crawling and independent source verification are intentionally not implemented.
-   Mock token counts are estimates and provider costs may be unknown; usage reporting currently retains up to 1,000 most recent events per workspace.
-   Platform previews are text-only approximations and do not guarantee platform capability or exact rendering.
-   Authentication has no email verification, password reset, refresh-token rotation, or identity-provider integration.
-   AI review and strategy outputs are advisory; they do not establish factual accuracy, legal compliance, content rights, or platform approval.
-   Media manifest metadata is versioned, but uploads/private storage and media processing are not implemented.
-   Campaign editing/deletion, content library media upload, calendar scheduling, notifications, provider-backed analytics/community sync, saved replies, and automated interaction classification remain incomplete.
-   Phase 4 analytics currently accepts explicitly labelled mock or manual imports; it does not fetch platform data. Reporting returns at most 500 matching snapshots and displays source metric values rather than inventing cross-platform aggregates. CSV export is available.
-   LinkedIn publishing permission does not imply post-read permission. The official Posts API describes `r_member_social` as restricted and available to approved users only; live post lookup/visibility and LinkedIn analytics remain disabled until that permission is granted and separately implemented. A create response/post URN confirms acceptance, not continued public visibility.
-   Community interactions are mock or manually imported. Provider inbox synchronization, AI classification, saved replies, and all external reply sending are unavailable. Approved reply drafts never trigger a provider call.
-   LinkedIn publish timeouts and server errors require manual verification. Owner/admin users can record a confirmed LinkedIn post URN or authorize a retry only after confirming no post exists; HTTP 429 responses are blocked until the operator waits for `Retry-After` and confirms no post was created. No blind automatic retries or provider-side post lookup are performed. LinkedIn read-post permissions may be restricted and are not requested.
-   Operator reconciliation is currently LinkedIn-specific and is not a substitute for provider-backed verification. Mock publishing remains idempotent; LinkedIn does not receive an assumed idempotency guarantee from its Posts API.
-   Production hosting, authentication provider, retention policy, and billing model remain undecided.
-   Real publishing depends on current platform API access, app review, OAuth scopes, account eligibility, and restrictions.

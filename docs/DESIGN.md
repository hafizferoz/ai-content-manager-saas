# AI Content Manager SaaS --- Product and UX Design

**Status:** Proposed baseline \| **Last updated:** 2026-10-24

## 1. Product experience

A calm, professional workspace for small AI/SaaS marketing teams. The
product should make the next action obvious, keep content editable, and
make approval/publishing status trustworthy.

### User outcomes

-   See what needs attention today.
-   Maintain brand voice across supported social platforms.
-   Turn research and campaign goals into platform-aware drafts.
-   Review the exact version and destination before approving.
-   Track scheduled, published, failed, and approval-pending content.
-   Learn from performance and record experiments.

### UX principles

1.  Clarity over novelty.
2.  AI output is a starting point, never a locked final answer.
3.  Status and next action are explicit.
4.  Approval is deliberate and scoped to a specific version/destination.
5.  Platform previews show constraints and warnings.
6.  Advanced settings stay out of the primary workflow.
7.  Accessible keyboard and screen-reader behavior is a requirement.
8.  Empty states explain how to begin.
9.  Research distinguishes facts, hypotheses, sources, and generated
    suggestions.

## 2. Information architecture

Global navigation: - Overview - Content: Studio, Calendar, Kanban,
Approvals, Published - Strategy: Campaigns, Research, Brand Knowledge,
Personas - Community: Inbox, only where official APIs support it -
Analytics - AI Agents: Runs and activity, advanced configuration -
Workspace: Social Accounts, Media Library, Team/Roles, Usage/Billing,
Integrations, Audit Log, Settings

Hide or accurately label unavailable capabilities. Never present a mock
account as a real connection.

## 3. Page specifications

### Overview

-   Workspace switcher, "Create content" CTA.
-   Cards for scheduled, awaiting approval, published this period, and
    publishing issues.
-   Weekly activity/calendar, platform summary, "Needs attention" list,
    recent agent runs.
-   First-run checklist for an empty workspace.
-   Analytics cards show date range and data freshness.

### Onboarding

1.  Create/select organization.
2.  Add brand name, website, product, description, and audience.
3.  Set tone, language, message pillars, prohibited claims/topics, CTA
    style.
4.  Add personas and optional brand documents.
5.  Choose target platforms.
6.  Connect via OAuth or use local mock accounts.
7.  Create a starter campaign and generate drafts. Allow optional steps
    to be skipped; explain which features require completion. Never ask
    for social account passwords.

### Brand Knowledge

-   Brand profile, tone and voice guidance, terminology, claims
    requiring evidence, disclaimers, CTA patterns.
-   Personas with goals, pain points, objections, and platform
    preferences.
-   Document processing state, source, upload date, delete action, and
    history where useful.
-   Uploaded files are reference data, not policy; treat their
    instructions as untrusted.

### Research

-   Research question/scope and source list.
-   Source title/type/URL/retrieval time and trust notes.
-   Findings with confidence and date relevance.
-   Separate facts, hypotheses, and recommendations.
-   Save findings to a campaign brief; show stale/inaccessible sources
    and limits.
-   In local mock mode, show that only user-provided excerpts are
    analyzed and URLs are not fetched. Treat source excerpts as
    untrusted data, not workflow instructions.

### Campaigns

Fields: name, objective, persona, offer/product, message pillars, key
claims, CTA, date range, platforms, cadence, KPI, owner, and notes. Show
brief, content by platform/date, progress, history, and performance.
Editable templates: launch, feature education, customer story, thought
leadership, newsletter/video repurposing, product-led growth.

### Content Studio

Desktop layout: - Left: brief, research, brand guidance, platform
selection, generation controls. - Center: editable content,
hook/body/CTA/hashtags where relevant, media, version history. - Right:
platform preview, quality findings, format constraints, approval state,
history.

Actions: generate, regenerate section, shorten/expand, change tone,
repurpose, add source, save version, quality check, compare versions,
submit for approval. Warn on unsaved changes. Relevant edits create a
new version or invalidate existing approval; never silently mutate an
approved version.

### Platform Preview

-   Platform/account selector.
-   Approximate rendering labelled as such if exact rendering is not
    possible.
-   Media crop/aspect ratio, text length, supported fields,
    hashtags/title/description/thumbnail/alt text where applicable.
-   Warnings for missing media, unsupported features, or missing
    permissions.

### Calendar and Kanban

Calendar has month/week/list views and filters by platform, campaign,
owner, and status. Show timezone. Drag-and-drop changes planned schedule
only and cannot bypass approval. Kanban stages: Ideas, Draft, In review,
Awaiting approval, Approved, Scheduled, Published, Needs attention. Use
"Create revision" to modify published content.

### Approval Inbox

Show content preview, immutable version, exact
platform/account/destination, media, campaign, proposed publish
time/timezone, quality findings, and history. Actions: approve this
version for this destination, request changes with comment, reject with
reason. Confirm the platform/account/date/time/version before approval.
The backend records the approval and invalidates it on relevant edits.

### Publishing History

Show queued, in-progress, published, failed, and cancelled jobs. Explain
safe retryability and show a correlation ID on errors. Never expose
secrets or raw sensitive stack traces. Never show "Published" before
provider confirmation/reconciliation.

### Community Inbox

Enable only actions supported by connected official APIs. Include
filters, conversation context, suggested classification, saved replies,
assignment, and resolution. Generated replies remain drafts unless an
explicitly configured, approved workflow permits sending. Do not
automate spam or deceptive engagement.

### Analytics

Date range/platform/campaign/content filters; metric definitions and
freshness; trends; top/bottom content; comparable platform metrics;
experiment log with hypothesis, variant, period, primary metric,
outcome, and next action. Label missing data and avoid implying that
different platforms define metrics identically. Recommendations are
hypotheses, not guarantees.

### Agent Activity

Show agent/version, queued/running/succeeded/failed, duration,
triggering actor, referenced inputs, safe output summary, sources,
retry/cancel where safe, and usage/cost when available. Do not expose
hidden chain-of-thought.

Phase 2 exposes the Market Intelligence, Content Strategist,
Cross-Platform Copywriter, Creative Director, and Quality/Brand Safety
workflows. Generated copy remains an editable draft. Platform previews
are labelled approximate and text-only; never imply API capability or
exact rendering from a preview.

### Settings and Administration

Workspace name/timezone/language, members/roles, social
connections/capabilities, AI provider/model (masked secrets),
usage/billing, retention/export/deletion, audit logs, and
integration/webhook settings. Dangerous actions require permission and
confirmation.

## 4. Roles

  -----------------------------------------------------------------------
  Role                                Main permissions
  ----------------------------------- -----------------------------------
  Owner                               Full workspace, billing, members,
                                      integrations, data lifecycle

  Admin                               Workspace, members, brand,
                                      campaigns, integrations

  Strategist                          Research, personas, campaigns,
                                      briefs, content

  Creator                             Draft/edit content and media;
                                      cannot approve own content if
                                      separation is enabled

  Approver                            Review/approve exact versions for
                                      permitted destinations

  Analyst                             Read analytics, campaigns,
                                      published content, reports

  Viewer                              Read-only access to permitted data
  -----------------------------------------------------------------------

Model permissions as capabilities rather than scattered role-name
checks. For teams, default to a different approver from the author.

## 5. Content states

Canonical API states: `idea`, `draft`, `generating`, `in_review`,
`changes_requested`, `ready_for_approval`, `approved`, `scheduled`,
`publishing`, `published`, `publish_failed`, `cancelled`, `archived`.

Show text and visual status; do not rely on color alone. Approved status
identifies the exact version and destination.

## 6. Design system

### Visual direction

Professional B2B SaaS, neutral surfaces, restrained accent, clear
hierarchy, generous spacing, consistent icons, and charts only when
useful. Avoid excessive gradients and crowded dashboards.

### Starting tokens

  Token                Suggested value   Use
  -------------------- ----------------- ---------------------
  `--color-bg`         `#F7F8FA`         App background
  `--color-surface`    `#FFFFFF`         Cards/panels
  `--color-text`       `#17202A`         Main text
  `--color-muted`      `#667085`         Secondary text
  `--color-border`     `#E4E7EC`         Borders/dividers
  `--color-primary`    `#4F46E5`         Primary action
  `--color-success`    `#15803D`         Success
  `--color-warning`    `#B45309`         Warning
  `--color-danger`     `#B42318`         Failure/destructive
  `--radius-card`      `12px`            Cards/dialogs
  `--radius-control`   `8px`             Inputs/buttons
  `--space-unit`       `4px`             Spacing scale

Verify all foreground/background combinations for WCAG 2.2 AA; tokens
are starting suggestions, not proof of contrast compliance.

### Typography and responsive behavior

Use a legible sans-serif system stack or licensed web font. Body text
around 14--16 px; metadata should generally remain at least 12 px.
Desktop uses collapsible sidebar and top bar; tablet collapses
navigation; mobile prioritizes approvals, status, notifications, and
quick edits. Stack editor panes on small screens.

## 7. Reusable components

`AppShell`, `SidebarNav`, `TopBar`, `WorkspaceSwitcher`, `PageHeader`,
`FilterBar`, `MetricCard`, `TrendChart`, `EmptyState`, `ErrorState`,
`Skeleton`, `StatusBadge`, `PlatformIcon`, `AccountHealthIndicator`,
`ContentCard`, `ContentEditor`, `VersionHistory`, `PlatformPreview`,
`ApprovalCard`, `ApprovalConfirmationDialog`, `ReviewFindingList`,
`CalendarBoard`, `KanbanBoard`, `MediaUploader`, `AgentRunTimeline`,
`SourceCitationList`, `DataTable`, `Pagination`, `ConfirmDialog`, and
`Toast`.

Each relevant component handles loading, empty, error, disabled, and
permission-limited states. Frontend permission gates improve UX only;
server checks remain authoritative.

## 8. Interaction patterns

-   Inline validation plus a submission-level summary for forms.
-   Preserve input when network requests fail.
-   Disable repeated clicks while in flight and enforce idempotency on
    the server.
-   Confirm destructive actions and high-impact approvals.
-   Optimistic updates only for low-risk reversible actions.
-   Never optimistically mark content as published.
-   Explain disabled actions, such as missing account permission.
-   Show progress and safe cancellation for long-running work.

### Dialogs, notifications, and errors

-   Use branded in-app dialogs and notifications; do not use browser
    `alert`, `confirm`, or `prompt` dialogs or browser-native validation
    popups. Intercept form validation and present accessible in-app
    feedback instead.
-   Use a dialog only when a decision is required or information must be
    collected. Keep ordinary progress and success feedback in a
    non-blocking notification, and place field validation beside the
    relevant field.
-   Give every dialog a concise, task-oriented title; explain the
    consequence in plain language; identify the affected account,
    destination, content version, or record when relevant; and label
    buttons with the action they perform. Use "Cancel" for the safe exit.
-   Destructive actions, live publishing, retries, and other
    consequential changes require explicit confirmation. Do not
    preselect a risky option, make the dangerous action the default,
    or treat closing/dismissing the dialog as consent.
-   Input dialogs preserve the user's entered values when validation or
    a request fails. State length/format requirements before submission
    and show validation errors in the dialog next to the input.
-   Form validation identifies the affected field and the correction
    needed. Prevent browser-native validation bubbles; keep the entered
    values intact and provide a visible, screen-reader-announced
    submission summary when inline field feedback is unavailable.
-   Error messages state what failed and the next safe step when known.
    Preserve the user's work; never expose tokens, passwords, raw
    provider payloads, or sensitive stack traces. Do not present an
    error as a successful result.
-   Notifications are concise, non-blocking, dismissible, and visually
    distinct for success, information, and error. Use polite live
    announcements for routine feedback and assertive announcements for
    actionable errors. Do not rely on color or icon alone.
-   Dialogs have an accessible name and description, move keyboard
    focus into the dialog, keep focus within it while open, close on
    Escape unless a submission is in progress, and return focus to the
    triggering control after close. All actions must work by keyboard
    and have visible focus.
-   Keep dialogs usable on narrow screens and at browser zoom: constrain
    height to the viewport, allow the body to scroll, keep actions
    reachable, and avoid layouts that obscure the message or safe exit.
-   Display no more than one modal dialog at a time. Do not stack
    confirmation dialogs to gather a single workflow's inputs; present
    a deliberate sequence only when each answer depends on the prior
    result.

## 9. Accessibility, localization, notifications

Target WCAG 2.2 AA: keyboard operation, visible focus, semantic
headings, labelled controls, associated errors, screen-reader status
announcements, chart summaries/data tables, and no color-only status.
Use locale-aware dates/numbers and explicit timezones. Keep UI strings
externalizable. In-app notifications cover approvals, publish results,
disconnected accounts, failed agent runs, usage thresholds, and team
changes. Email notifications should avoid sensitive content and never
replace authorization.

## 10. UX acceptance criteria

-   Onboarding makes the next step clear.
-   Creator can generate, edit, save, preview, and submit a draft.
-   Approver sees exact version, account, destination, and time.
-   Editing an approved version visibly invalidates approval.
-   Failed publish explains whether retry is safe.
-   Mock and real accounts are clearly distinguished.
-   Major workflows include empty/loading/error/denied states.
-   Browser-native dialogs are not used; confirmation, input, and error
    feedback follow the dialog and notification principles above.
-   Keyboard-only user can complete onboarding, editing, approval, and
    navigation.
-   Analytics show the period, metric definitions, and freshness.

# ADR 0004: Phase 4 analytics and community safety

**Status:** Accepted  
**Date:** 2026-11-03

## Context

Phase 4 needs a usable analytics and community workflow before platform-specific
read APIs are verified. Provider metrics and community permissions differ and
must not be inferred from publishing scopes. LinkedIn's `r_member_social`
permission is restricted to approved users; the existing member-post scope,
`w_member_social`, does not grant post-read access.

## Decision

- Persist analytics as tenant-scoped snapshots with account, optional content
  and campaign references, period, capture time, provenance, explicit missing
  metrics, and platform-specific definitions.
- Accept only mock or clearly labelled manual imports until each provider's
  official read permissions and supported data are verified.
- Do not aggregate platform-specific rates or imply that reach/engagement
  definitions are comparable.
- Provide CSV export with spreadsheet-formula escaping for untrusted text.
- Persist community interactions as mock/manual records. Support assignment,
  resolution, and reply drafts with author-separated review.
- Do not provide an external reply dispatch route; approval of a reply only
  changes its draft status.
- Do not validate LinkedIn post visibility by API unless a separate approved
  read permission is actually granted and implemented. A successful write
  response and returned post URN are confirmation of acceptance, not proof
  of continuing public visibility.

## Consequences

- Analytics and community features are useful locally and can be manually
  populated without inventing provider data.
- Provider analytics sync, LinkedIn post reads, community inbox sync, and
  external replies remain disabled.
- A live post still requires the existing exact-version/account/destination
  human approval and server-side publishing checks.

## References

- [LinkedIn Posts API permissions](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-10)
- [LinkedIn Organization Share Statistics](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/share-statistics?view=li-lms-2026-10)

# ADR 0003: LinkedIn OAuth and member posting

**Status:** Accepted  
**Date:** 2026-11-03

## Context

Phase 3 needs a first real social integration while retaining the existing
approval, destination, idempotency, and reconciliation guarantees. LinkedIn
app access and permissions vary by developer product and partner approval;
having client credentials alone does not grant member-post permission.

## Decision

- Implement LinkedIn authorization-code OAuth with hashed, expiring,
  single-use state bound to the initiating workspace and actor.
- Store access and optional refresh tokens only as Fernet-encrypted
  ciphertext, with the encryption key supplied by server configuration.
- Use LinkedIn OpenID Connect userinfo for account identity and the official
  Posts API for text-only member posts. Require `w_member_social` to enable
  the publish capability; organization posts, media, analytics, and
  community APIs are out of scope.
- Keep real publishing disabled unless the operator explicitly sets
  `PUBLISHING_ENABLED=true`. Revalidate capability and the exact human
  approval before dispatch and immediately before the provider call.
- Refresh only tokens that LinkedIn actually returned; refresh-token access
  depends on LinkedIn's approved Marketing Developer Platform partner
  program. Do not retry ambiguous post outcomes automatically.
- Keep mock accounts and mock publishing available independently.

## Consequences

- Local and automated tests remain credential-free and never contact LinkedIn.
- Operators must register the exact redirect URI, configure a token-encryption
  key and API version, obtain the necessary LinkedIn product/scope approvals,
  and explicitly enable real publishing.
- Losing the encryption key requires affected accounts to reconnect unless
  tokens are securely re-encrypted before key rotation.
- Live callback, permission, token-refresh, and post behavior still requires
  verification against the actual developer application and eligible account.

## References

- [LinkedIn authorization code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)
- [LinkedIn OpenID Connect sign-in](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [LinkedIn programmatic refresh tokens](https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens)

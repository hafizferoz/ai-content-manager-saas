# ADR 0005: Workspace multi-provider AI configuration

**Status:** Accepted  
**Date:** 2026-11-03

## Context

The AI workflow already has a synchronous structured-provider boundary and stores organization-scoped agent runs and usage events. The initial implementation used a deterministic mock or one server-configured OpenAI-compatible provider. Provider credentials must remain server-side, and content approval/publishing must remain independent from AI generation.

## Decision

- Preserve the existing `StructuredProvider` workflow boundary and typed output validation.
- Add Gemini's native generate-content adapter and OpenAI-compatible adapters for OpenAI, Groq, OpenRouter, Mistral, and compatible deployments.
- Store customer-configured API credentials per organization, encrypted with a dedicated `AI_ENCRYPTION_KEY`; only owners/admins may manage them.
- Keep existing environment configuration as a platform default for backward compatibility. Do not silently mix platform credentials into an organization's BYOK fallback chain.
- Order workspace providers by configured priority and cap attempts with `AI_MAX_PROVIDER_ATTEMPTS`.
- Fall back only after transient rate-limit, quota, timeout, network, or provider-service errors. Authentication, validation, unsupported-model, and content-policy failures stop the chain.
- Record sanitized provider attempt outcomes, final provider/model, and fallback usage on the agent run. Never retain provider response bodies or credentials.
- Keep AI output as an editable draft; the existing human approval and publishing controls remain unchanged.

## Consequences

- Local development and tests continue to work with the deterministic mock and require no real provider credentials.
- Workspace API keys are not recoverable if `AI_ENCRYPTION_KEY` is lost. Operators must protect and back up that key separately from the database, and should plan key rotation.
- Provider model catalogs and account availability differ. Discovery is advisory; providers may still reject an unsupported model or structured-output mode.
- Usage pricing, budget enforcement, full provider-health monitoring, and organization-wide usage analytics remain future work.

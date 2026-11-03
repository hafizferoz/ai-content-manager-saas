# ADR 0002: Phase 2 AI workflow boundary

**Status:** Accepted  
**Date:** 2026-11-03

## Context

Phase 2 needs usable research, strategy, writing, creative, and quality-review workflows without making local setup depend on an external model or granting AI authority to approve/publish.

## Decision

- Define a structured provider interface with a deterministic local mock and an optional server-configured OpenAI-compatible chat-completions adapter.
- Require Pydantic schema validation on provider output and reject source citations outside the active workspace workflow.
- Treat user prompts and source excerpts as untrusted data in workflow instructions; never fetch research URLs in this phase.
- Persist tenant-scoped agent runs, sanitized failure codes, linked output, duration, and token-usage events. Do not retain hidden reasoning or provider credentials.
- Keep generated content as a draft and keep AI quality review advisory. Existing deterministic checks and human approval remain enforced independently.
- Label platform previews approximate; no preview enables publishing or asserts real account capability.

## Consequences

- Developers can exercise AI workflows without API credentials; external model usage requires explicit server-side configuration.
- Customers must review AI provider data-handling terms before sending content or research excerpts.
- Actual provider cost may be unknown; mock token counts are estimates.
- Source retrieval, factual verification, evaluation datasets, budgets, and provider-specific contract tests remain future work.

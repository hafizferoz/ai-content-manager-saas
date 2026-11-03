# AI Provider Architecture

The API keeps the existing structured-generation boundary in
`apps/api/app/ai_provider.py`. AI workflows validate provider output with
Pydantic schemas before recording a run or creating a draft.

Supported modes:

- `mock`: deterministic and credential-free, used by default in local development.
- `gemini`: Google Gemini `generateContent` with JSON response MIME type.
- `openai`, `groq`, `openrouter`, and `mistral`: OpenAI-compatible chat-completions adapters.
- `openai-compatible`: existing environment-configured endpoint for backward compatibility.

Workspace owners/admins can configure BYOK credentials from **AI Providers**.
Credentials are encrypted in PostgreSQL with `AI_ENCRYPTION_KEY`, and
configuration responses contain only whether a key is present. Providers
are ordered by priority. The bounded fallback runner records the attempted
providers and selected provider/model in the agent run.

```text
AI Studio request
  -> workspace provider configuration (or platform environment default)
  -> primary provider
  -> optional transient-error fallback, bounded by AI_MAX_PROVIDER_ATTEMPTS
  -> typed output validation and source scoping
  -> editable draft + agent/usage records
  -> existing quality review and human approval workflow
```

Provider errors remain safe summaries; response bodies and credentials are
not stored. No AI provider can approve or publish content. See
[ADR 0005](./adr/0005-multi-provider-ai-configuration.md).

Not implemented yet: model capability catalogs beyond IDs, provider health
monitoring, pricing, spend limits, and full usage analytics.

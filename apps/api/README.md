# API and publishing worker

FastAPI application and worker source are in `app/`. The service uses SQLAlchemy 2 and Alembic; configure `DATABASE_URL`, apply migrations using `alembic upgrade head`, then launch `uvicorn app.main:app`. The worker is `python -m app.worker` and must share the API database.

Local authentication uses Argon2 password hashes and opaque hashed bearer sessions. All workspace resources are authorized by an active membership on the server. LinkedIn is the only implemented real social connection; all other social providers remain mock-only.

## Phase 2 AI workflows

- `POST /api/v1/workspaces/{id}/research` stores user-supplied source metadata/excerpts and generates source-linked findings. It never fetches a supplied URL.
- `POST /api/v1/workspaces/{id}/campaigns/{campaign_id}/strategy` generates a structured campaign strategy.
- Generated strategies are persisted as tenant-scoped campaign history with source IDs and agent-run references; campaign listing returns the latest strategy and `GET /api/v1/workspaces/{id}/campaigns/{campaign_id}/strategies` returns recent history.
- `POST /api/v1/workspaces/{id}/content/generate` creates an editable content draft and immutable source-linked version.
- `POST /api/v1/workspaces/{id}/campaigns/{campaign_id}/creative-concepts` generates structured creative proposals.
- `POST /api/v1/workspaces/{id}/content/{item_id}/ai-review` stores an advisory review. Blocking findings prevent approval submission until a new passing review is recorded.
- `GET /api/v1/workspaces/{id}/agent-runs` and `/usage` expose tenant-scoped run history and token usage.
- `GET /api/v1/workspaces/{id}/content/{item_id}/platform-preview` returns an approximate, text-only preview.
- `GET /api/v1/workspaces/{id}/integrations/capabilities` lists the per-platform adapter capability baseline. `GET /api/v1/workspaces/{id}/social-accounts` returns each account's persisted capability state.
- `POST /api/v1/workspaces/{id}/integrations/linkedin/connect` starts a workspace-admin OAuth flow; LinkedIn redirects to `/api/v1/integrations/linkedin/callback`. `DELETE /api/v1/workspaces/{id}/social-accounts/{account_id}/connection` deletes locally stored credentials and disables that account.

The Phase 3 registry enables mock publishing for local accounts. LinkedIn can be connected through server-side authorization-code OAuth when configured. Only text-only member posts are implemented; the LinkedIn publish capability is enabled only when the token grants `w_member_social`, and the usual immutable-version human approval gate still applies. Organization posts, media, LinkedIn analytics, community features, and all other real-platform adapters remain unavailable.

An uncertain LinkedIn job is marked `reconciliation_required` and is never retried automatically. Workspace owners/admins can use `POST /api/v1/workspaces/{id}/publish-jobs/{job_id}/reconcile` after manually checking LinkedIn: record a confirmed `urn:li:share:...`/`urn:li:ugcPost:...`, or explicitly confirm no post exists before retrying. HTTP 429 responses are blocked; wait for LinkedIn's `Retry-After` period and confirm no post exists before authorizing a retry. Each operator action is audited. Keep the evidence summary free of credentials and tokens. This does not query LinkedIn automatically; the application does not request restricted read-post permissions.

For local Docker development, set `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI`, `LINKEDIN_API_VERSION`, and `SOCIAL_TOKEN_ENCRYPTION_KEY` in the ignored `.env` file. Register the exact redirect URI in LinkedIn's Developer Portal. Set `PUBLISHING_ENABLED=true` before connecting an account only after confirming the app has the necessary approved LinkedIn product and member-post permission; it defaults to `false`. If you connect while publishing is disabled, disconnect/reconnect after enabling it. Generate a Fernet-compatible encryption key locally with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy that generated key into `SOCIAL_TOKEN_ENCRYPTION_KEY`; never commit it or share it. The API and worker both need the same encryption key. Losing or rotating the key without re-encrypting tokens makes existing credentials unusable; disconnect and reconnect affected accounts. `LINKEDIN_API_VERSION` uses LinkedIn's `YYYYMM` REST version header. Check the current LinkedIn developer documentation and your app's approved Products/scopes before changing it.

The OAuth callback uses a hashed, expiring, single-use state, verifies the initiating workspace membership remains active, and never returns tokens to the browser. Tokens are encrypted at rest; the real adapter is restricted to the official Posts API, member authors, and text-only content. Ambiguous network outcomes are marked for owner/admin reconciliation rather than automatically retried. Refresh is attempted only if LinkedIn issued a refresh token; availability of refresh tokens is app/program dependent.

Implementation follows LinkedIn's [authorization code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow), [OpenID Connect userinfo](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2), [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api), and [programmatic refresh-token requirements](https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens). Verify these requirements and your app's approved scopes before enabling production publishing.

The default provider is deterministic `mock`; it makes no external AI calls. An OpenAI-compatible provider can be selected using server-side `LLM_PROVIDER=openai-compatible`, `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL`. Remote provider URLs must use HTTPS. Prompts and supplied excerpts may contain customer data, so only configure a provider after reviewing its data-handling terms. The adapter validates structured output and source references; it does not store hidden reasoning or credentials.

## Phase 2 evaluation

From `apps/api`, run the versioned five-workflow evaluation corpus against the mock provider:

```powershell
python -m app.evaluate_ai
```

The runner validates each result against the same Pydantic output schema used by the API, then checks citation scoping and workflow-specific expectations such as cited research findings, prohibited copy phrases, and blocking brand-safety findings. It prints a JSON report and exits non-zero if any check fails. These are deterministic regression checks, not a substitute for human quality review or factual verification.

To evaluate a configured OpenAI-compatible provider, set the provider environment variables described above and explicitly opt in:

```powershell
$env:LLM_PROVIDER = "openai-compatible"
python -m app.evaluate_ai --allow-live-provider
```

This sends the synthetic corpus inputs to the configured provider and may incur costs. Never put customer content or secrets in the evaluation corpus. Live provider evaluation is opt-in; normal tests and the default runner do not make external AI calls.

See the repository [README](../../README.md) for setup, routes, and test commands.

# API and publishing worker

FastAPI application and worker source are in `app/`. The service uses SQLAlchemy 2 and Alembic; configure `DATABASE_URL`, apply migrations using `alembic upgrade head`, then launch `uvicorn app.main:app`. The worker is `python -m app.worker` and must share the API database.

Local authentication uses Argon2 password hashes and opaque hashed bearer sessions. All workspace resources are authorized by an active membership on the server. Publishing is mock-only; do not configure real credentials or claim external API support.

## Phase 2 AI workflows

- `POST /api/v1/workspaces/{id}/research` stores user-supplied source metadata/excerpts and generates source-linked findings. It never fetches a supplied URL.
- `POST /api/v1/workspaces/{id}/campaigns/{campaign_id}/strategy` generates a structured campaign strategy.
- Generated strategies are persisted as tenant-scoped campaign history with source IDs and agent-run references; campaign listing returns the latest strategy and `GET /api/v1/workspaces/{id}/campaigns/{campaign_id}/strategies` returns recent history.
- `POST /api/v1/workspaces/{id}/content/generate` creates an editable content draft and immutable source-linked version.
- `POST /api/v1/workspaces/{id}/campaigns/{campaign_id}/creative-concepts` generates structured creative proposals.
- `POST /api/v1/workspaces/{id}/content/{item_id}/ai-review` stores an advisory review. Blocking findings prevent approval submission until a new passing review is recorded.
- `GET /api/v1/workspaces/{id}/agent-runs` and `/usage` expose tenant-scoped run history and token usage.
- `GET /api/v1/workspaces/{id}/content/{item_id}/platform-preview` returns an approximate, text-only preview.

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

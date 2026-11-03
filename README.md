# AI Content Manager SaaS

A working Phase 1 foundation, Phase 2 AI workflows, Phase 3 LinkedIn publishing support, and a Phase 4 mock/manual analytics and community workflow slice. The application includes an authenticated FastAPI API, a Next.js dashboard, PostgreSQL migrations, a database-backed publishing worker, an idempotent local mock social adapter, and structured research/strategy/drafting/review agents.

## What works today

- Email/password sign-up and login with Argon2 password hashing and expiring opaque bearer sessions.
- Workspace creation, role-based memberships, member management, and tenant-scoped API access.
- Brand profile and campaign creation.
- Immutable content versions, content revision, configurable basic quality checks, and version history.
- Approval inbox bound to content version/fingerprint, mock platform account, destination, and `publish` action. Authors cannot approve their own content.
- A persistent publish queue with idempotency, approval/approver/account revalidation, audit events, and mock provider confirmation.
- Responsive web dashboard to exercise the workflow without platform credentials.
- PostgreSQL/Redis/API/worker/web Docker Compose setup.
- Source-linked research and structured Market Intelligence, Content Strategist, Cross-Platform Copywriter, Creative Director, and Quality/Brand Safety workflows.
- Persisted campaign strategy history with source and agent-run traceability.
- Tenant-scoped social capability registry, mock adapter, and a limited LinkedIn OAuth/text-post adapter.
- Tenant-scoped agent run history, token usage records, and approximate text-only platform previews.
- Tenant-scoped analytics snapshots with metric definitions, freshness, filters, CSV export, and experiment logs; snapshots are mock/manual and are not live provider syncs.
- A mock/manual community inbox with assignment, resolution, and human-reviewed reply drafts; no replies are sent externally.

LinkedIn authorization and a limited text-only member-post adapter are implemented, but real calls require a correctly configured callback and encryption key, an approved LinkedIn app/product, and `w_member_social` permission. The styled approval dialog lists all matching workspace accounts, labels mock/live status, and explains when a live account is unavailable due to permissions or publishing configuration. Select the exact destination explicitly; accounts are not shared across workspaces. `USE_MOCK_SOCIAL_ADAPTER` is not a global switch; adapter selection follows whether the selected account is mock or OAuth-connected. All other real social APIs, provider-backed analytics/community ingestion, external community replies, file uploads, calendar scheduling, and production identity-provider integration remain unavailable. Mock AI is the default; an optional server-configured OpenAI-compatible LLM provider is supported. The mock social adapter never sends an external post.

## Run with Docker Compose

Requires Docker Desktop with Compose. Local defaults are for development only.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000) for the web app and [http://localhost:8000/docs](http://localhost:8000/docs) for the API docs. Liveness and readiness checks are at `/health/live` and `/health/ready`. Compose runs Alembic migrations before starting the API and starts the publishing worker against the same database.

Sign up, create a mock social account and campaign, create a draft, run its quality check, then request approval. To complete approval, register a second user, sign in as the workspace owner, add that user with the `approver` role under **Team**, then sign in as the approver and approve the pending item. The worker will process queued jobs automatically; the **Publishing** page also has a manual mock dispatch action for local workflow testing.

To try Phase 2, add a research question and source excerpt under **Research**, then use the research record ID in **AI Studio** to generate strategy, creative concepts, or a platform-specific draft. AI-generated content is an editable draft. Review the structured source citations, run both quality checks as needed, and retain the human approval step before publishing.

The default `LLM_PROVIDER=mock` does not send data externally. To opt into a configured OpenAI-compatible provider, set `LLM_PROVIDER=openai-compatible`, `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL` in the server `.env`, then restart the stack. Remote provider URLs must use HTTPS. Review the provider’s data-handling terms before sending customer content or source excerpts.

For LinkedIn, configure its registered callback URI, `LINKEDIN_API_VERSION`, a generated `SOCIAL_TOKEN_ENCRYPTION_KEY`, and `PUBLISHING_ENABLED=true` only after verifying the approved app permissions. Follow [apps/api/README.md](apps/api/README.md); client credentials alone do not grant publishing access.

## Run services locally without Docker

Requires Python 3.12+ and Node.js 20+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\apps\api[test]"
$env:DATABASE_URL = "sqlite:///./content_manager.db"
Push-Location apps/api
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

In another PowerShell terminal, start the worker:

```powershell
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL = "sqlite:///./content_manager.db"
Push-Location apps/api
python -m app.worker
```

In a third terminal, start the web app:

```powershell
Push-Location apps/web
npm install
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev
```

For PostgreSQL locally, start `postgres` from Compose and set `DATABASE_URL` to the connection string in `.env.example`. The worker and API must use the same database URL.

## Tests and checks

```powershell
Push-Location apps/api
python -m pip install -e ".[test]"
pytest
alembic upgrade head
Pop-Location

Push-Location apps/web
npm install
npm run lint
npm run build
Pop-Location
```

The API tests use isolated SQLite databases and cover migrations from the Phase 1 schema, source-linked research, structured AI workflows, tenant isolation, provider errors, quality blocks, approval invalidation, and mock publishing.

The Phase 2 synthetic AI evaluation corpus can be run from `apps/api` with `python -m app.evaluate_ai`. It uses the mock provider by default; evaluating a configured live provider requires the explicit `--allow-live-provider` flag and may incur costs. See [apps/api/README.md](apps/api/README.md) for details.

## Security and production notes

- Set strong local-only credentials in `.env`; do not commit it.
- The starter is not yet a production deployment: add rate limiting, email verification/password recovery, managed secrets, retention/deletion controls, monitoring, backup/restore, and a security review before real customer data.
- Real publishing is currently limited to the optional LinkedIn text-only member-post adapter; other platforms remain mock-only. Real publishing requires an approved human action, the exact connected account/destination, granted member-post permission, and explicit operator enablement.
- Uncertain LinkedIn outcomes are not retried automatically. Workspace owners/admins must manually verify the outcome before recording a confirmed post URN or authorizing a retry; rate-limited requests require waiting for `Retry-After` and confirming no post was created.

See [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for phase tracking and limitations, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system boundaries, and [docs/DESIGN.md](docs/DESIGN.md) for interaction intent.

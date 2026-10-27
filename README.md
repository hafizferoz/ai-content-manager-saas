# AI Content Manager SaaS

A working Phase 1 vertical slice for multi-tenant social content workflows. The application includes an authenticated FastAPI API, a Next.js dashboard, PostgreSQL migrations, a database-backed publishing worker, and an idempotent local mock social adapter.

## What works today

- Email/password sign-up and login with Argon2 password hashing and expiring opaque bearer sessions.
- Workspace creation, role-based memberships, member management, and tenant-scoped API access.
- Brand profile and campaign creation.
- Immutable content versions, content revision, configurable basic quality checks, and version history.
- Approval inbox bound to content version/fingerprint, mock platform account, destination, and `publish` action. Authors cannot approve their own content.
- A persistent publish queue with idempotency, approval/approver/account revalidation, audit events, and mock provider confirmation.
- Responsive web dashboard to exercise the workflow without platform credentials.
- PostgreSQL/Redis/API/worker/web Docker Compose setup.

Real social network APIs, file uploads, calendar scheduling, AI provider integrations, analytics ingestion, and production identity-provider integration are not enabled. The mock adapter never sends an external post.

## Run with Docker Compose

Requires Docker Desktop with Compose. Local defaults are for development only.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000) for the web app and [http://localhost:8000/docs](http://localhost:8000/docs) for the API docs. Liveness and readiness checks are at `/health/live` and `/health/ready`. Compose runs Alembic migrations before starting the API and starts the publishing worker against the same database.

Sign up, create a mock social account and campaign, create a draft, run its quality check, then request approval. To complete approval, register a second user, sign in as the workspace owner, add that user with the `approver` role under **Team**, then sign in as the approver and approve the pending item. The worker will process queued jobs automatically; the **Publishing** page also has a manual mock dispatch action for local workflow testing.

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

The API tests use an isolated in-memory SQLite database and cover registration, the draft/review/approval/mock-publish journey, tenant isolation, author/approver controls, approval invalidation, revoked approvers, quality blocks, and idempotent dispatch.

## Security and production notes

- Set strong local-only credentials in `.env`; do not commit it.
- The starter is not yet a production deployment: add rate limiting, email verification/password recovery, managed secrets, retention/deletion controls, monitoring, backup/restore, and a security review before real customer data.
- The app deliberately refuses non-mock accounts. Enable a real provider only after implementing its official API, permissions, secure OAuth/token storage, adapter contract tests, and operational reconciliation.
- No publication is marked successful until the mock provider returns a confirmation.

See [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for phase tracking and limitations, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system boundaries, and [docs/DESIGN.md](docs/DESIGN.md) for interaction intent.

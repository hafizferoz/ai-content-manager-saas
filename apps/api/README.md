# API and publishing worker

FastAPI application and worker source are in `app/`. The service uses SQLAlchemy 2 and Alembic; configure `DATABASE_URL`, apply migrations using `alembic upgrade head`, then launch `uvicorn app.main:app`. The worker is `python -m app.worker` and must share the API database.

Local authentication uses Argon2 password hashes and opaque hashed bearer sessions. All workspace resources are authorized by an active membership on the server. Publishing is mock-only; do not configure real credentials or claim external API support.

See the repository [README](../../README.md) for setup, routes, and test commands.

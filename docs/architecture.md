# Architecture

Monorepo:

- `apps/web` — Next.js App Router UI
- `apps/api` — FastAPI, SQLAlchemy, Alembic
- `workers` — Celery tasks (scrape, crawl, send)
- `packages/shared` — shared constants
- `infrastructure/docker` — images
- `docs` — operations and OAuth

Multi-tenant rule: every tenant-owned row has `user_id`. Routers load the current user from JWT and filter by that id. Gmail tokens are encrypted per `email_accounts` row and are never returned to the browser.

Long work (Maps scrape, website analysis, campaign sending) runs in Celery, not inside HTTP request handlers.

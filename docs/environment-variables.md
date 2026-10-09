# Environment variables

Copy `.env.example` to `.env` at the repo root.

Required for a working local stack:

- `DATABASE_URL` — PostgreSQL SQLAlchemy URL
- `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`
- `SECRET_KEY` — JWT signing
- `ENCRYPTION_KEY` — Fernet key for OAuth tokens (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- `FRONTEND_URL` / `BACKEND_URL`

Optional integrations (real code is present; features show a configuration-required state if empty):

- `AI_API_KEY`, `AI_API_BASE`, `AI_MODEL`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
- `APIFY_TOKEN` — Google Maps via [Apify](https://apify.com/compass/crawler-google-places/api). If set, Find leads does not use Chrome.

Frontend: `apps/web/.env.local` with `NEXT_PUBLIC_API_URL=http://localhost:8000`.

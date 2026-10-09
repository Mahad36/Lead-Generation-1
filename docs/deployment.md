# Production deployment

1. Provision PostgreSQL, Redis, HTTPS reverse proxy, and a VPS or container host.
2. Set production `FRONTEND_URL`, `BACKEND_URL`, `GOOGLE_REDIRECT_URI`, strong `SECRET_KEY` and `ENCRYPTION_KEY`.
3. Run `alembic upgrade head`.
4. Run API, Celery worker, Celery beat, and the Next.js production server (or export behind the same domain).
5. Configure SPF, DKIM, and DMARC on **each user's sending domain** — this app sends through their Gmail, so Google's own auth still applies.
6. Add Privacy Policy, Terms, and the unsubscribe page.
7. Back up PostgreSQL and monitor worker failures.

Do not hard-code localhost. All public URLs come from environment variables.

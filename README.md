# Lead Generator

Public multi-tenant SaaS for Google Maps lead collection, website analysis, AI personalization, and **per-user Gmail** outreach.

Each registered user owns their leads, templates, campaigns, and connected Gmail accounts. There is no global inbox.

## Requirements

- Node.js 20+
- Python 3.12+
- PostgreSQL 16
- Redis 7
- Chrome (Selenium)

## Environment

```powershell
copy .env.example .env
```

Generate `ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Frontend:

```powershell
copy apps\web\.env.local.example apps\web\.env.local
```

See `docs/environment-variables.md`, `docs/google-oauth-setup.md`.

## Database migrations

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python ..\..\scripts\seed.py
```

## Start services

PostgreSQL + Redis (Docker only for data stores):

```powershell
docker compose up -d postgres redis
```

API:

```powershell
cd apps\api
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

Celery worker (from repo root; `--pool=solo` is required on Windows):

```powershell
$env:PYTHONPATH="apps\api;."
celery -A workers.celery_app.celery_app worker --loglevel=info --pool=solo
```

Celery beat:

```powershell
$env:PYTHONPATH="apps\api;."
celery -A workers.celery_app.celery_app beat --loglevel=info
```

Frontend:

```powershell
cd apps\web
npm install
npm run dev
```

Open http://localhost:3000 — seed user `demo@leadgen.local` / `DemoPass123!`

## Run on Windows without Docker

Double-click `setup-windows.bat` once, then `start-windows.bat`. Everything (database included) runs on your PC,
together with the automatic audit poller. Full guide: `docs/windows.md`.

## Try it with no installs: GitHub Codespaces

On GitHub: **Code → Codespaces → Create codespace** on this branch. Everything installs and starts automatically
(about 5–8 minutes the first time); then open the **App (3000)** port. Full guide: `docs/codespaces.md`.

## Docker (full stack) — easiest way to test everything locally

Needs only [Docker Desktop](https://www.docker.com/products/docker-desktop/). No `.env` file is required.

```powershell
docker compose up --build
```

The first build takes a few minutes. When the logs show `Uvicorn running`, open:

| URL | What |
| --- | --- |
| http://localhost:3000 | The app. Log in with `demo@leadgen.local` / `DemoPass123!` |
| http://localhost:8025 | Test inbox (Mailpit). Every email the app sends lands here, with attachments |
| http://localhost:8000/docs | API docs |

Test the website audit workflow end to end:

1. **AI & settings** → provider *Claude (Anthropic)* → paste your Claude API key → Save.
2. **Website audits** → enter a business name, its website URL and any customer email → **Run website audit**.
   Watch the steps complete (1–3 minutes).
3. Review the score, open the PDF, edit the email if you like → **Send email with PDF**.
4. Open http://localhost:8025 to see the email exactly as the customer would, with the PDF attached.

Sending goes to the test inbox by default (account *Test inbox (Mailpit)* in **Email setup**), so nothing reaches real
people. To send for real, connect Gmail or SMTP in **Email setup** and make it the default.

Stop with `Ctrl+C`; `docker compose down -v` also deletes the database.

## Tests

```powershell
cd apps\api
.\.venv\Scripts\Activate.ps1
pytest
```

```powershell
cd apps\web
npx tsc --noEmit
```

## Google OAuth / Gmail / AI

Documented in `docs/google-oauth-setup.md`, `docs/gmail-setup.md`. Leave keys empty to keep those features in a configuration-required state. The app will not fake sends or AI output.

## Known limitations

- Google Maps scraping can be blocked; the job stops and keeps already saved leads. No CAPTCHA bypass.
- Gmail sending requires a verified Google Cloud OAuth app for public users.
- Open tracking is off; open rate is reported as unknown.
- Email verification is optional and stays `not_checked` without a provider.

Website audit + PDF + outreach email workflow: `docs/website-audit.md`.

More: `docs/local-development.md`, `docs/architecture.md`, `docs/deployment.md`, `docs/security.md`.

# Local development

## Requirements

- Python 3.12+
- Node.js 20+
- PostgreSQL 16
- Redis 7
- Google Chrome (for Selenium)

## Backend (PowerShell)

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..\..
copy .env.example .env
# edit .env
cd apps\api
alembic upgrade head
python ..\..\scripts\seed.py
uvicorn app.main:app --reload --port 8000
```

## Celery

```powershell
$env:PYTHONPATH="apps\api;."
celery -A workers.celery_app.celery_app worker --loglevel=info --pool=solo
celery -A workers.celery_app.celery_app beat --loglevel=info
```

## Frontend

```powershell
cd apps\web
copy .env.local.example .env.local
npm install
npm run dev
```

Demo login after seed: `demo@leadgen.local` / `DemoPass123!`

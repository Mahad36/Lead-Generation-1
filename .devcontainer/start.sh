#!/usr/bin/env bash
# Starts everything (runs on every Codespace start). Safe to run again: `bash .devcontainer/start.sh`
# Logs: /tmp/leadgen-logs/*.log
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS=/tmp/leadgen-logs
mkdir -p "$LOGS"
cd "$ROOT"

step() { echo "==> $*"; }
# fail "message" [logfile]: print the problem and the end of the relevant log, then stop.
fail() {
  echo ""
  echo "!! $1"
  if [ -n "${2:-}" ] && [ -f "$LOGS/$2" ]; then
    echo "---- last lines of $LOGS/$2 ----"
    tail -n 25 "$LOGS/$2"
  fi
  echo "!! Fix or share the message above, then run: bash .devcontainer/start.sh"
  exit 1
}
bg() { local name=$1; shift; setsid nohup "$@" >"$LOGS/$name.log" 2>&1 < /dev/null & }

if [ -n "${CODESPACE_NAME:-}" ]; then
  APP_URL="https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
  INBOX_URL="https://${CODESPACE_NAME}-8025.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
else
  APP_URL="http://localhost:3000"
  INBOX_URL="http://localhost:8025"
fi

export ENVIRONMENT=development
export SECRET_KEY="${SECRET_KEY:-codespace-dev-secret-$(hostname)}"
export DATABASE_URL=postgresql+psycopg://leadgen:leadgen@localhost:5432/leadgen
export REDIS_URL=redis://localhost:6379/0 CELERY_BROKER_URL=redis://localhost:6379/0 CELERY_RESULT_BACKEND=redis://localhost:6379/1
export FRONTEND_URL="$APP_URL" BACKEND_URL=http://localhost:8000
# Google sends the browser back here after Gmail sign-in; /api/* on the app URL is forwarded to the API.
export GOOGLE_REDIRECT_URI="$APP_URL/api/email-accounts/gmail/callback"
export AI_PROVIDER="${AI_PROVIDER:-anthropic}" AI_API_BASE="${AI_API_BASE:-https://api.anthropic.com}" AI_MODEL="${AI_MODEL:-claude-opus-5-5}"
export TEST_INBOX_SMTP_HOST=localhost TEST_INBOX_SMTP_PORT=1025
export NEXT_PUBLIC_API_URL=http://localhost:8000 API_INTERNAL_URL=http://localhost:8000
export PYTHONPATH="$ROOT/apps/api:$ROOT"
export PATH="$HOME/.local/bin:$PATH"  # Claude Code CLI
# Google Maps scraping without Apify drives Debian's Chromium with its matching driver.
export CHROME_BINARY=/usr/bin/chromium CHROMEDRIVER_PATH=/usr/bin/chromedriver HEADLESS=true

if [ ! -f apps/api/.venv/.setup-complete ] || ! command -v psql >/dev/null || ! command -v mailpit >/dev/null; then
  step "First-time setup has not finished; running it now (several minutes)"
  bash .devcontainer/setup.sh 2>&1 | tee "$LOGS/setup.log" || true
  [ -f apps/api/.venv/.setup-complete ] || fail "Setup did not finish" setup.log
fi

step "Stopping any previous run"
pkill -f "[u]vicorn app.main:app" ; pkill -f "[c]elery -A workers.celery_app" ; pkill -f "[n]ext start" ; pkill -f "[n]ext-server"
sleep 1

step "Starting PostgreSQL, Redis and the test inbox"
sudo -n service postgresql start >/dev/null || fail "PostgreSQL did not start"
sudo -n service redis-server start >/dev/null || redis-server --daemonize yes >/dev/null || fail "Redis did not start"
pgrep -x mailpit >/dev/null || bg mailpit mailpit --smtp-auth-accept-any --smtp-auth-allow-insecure
for _ in $(seq 1 30); do pg_isready -q -h localhost && break; sleep 1; done

step "Preparing the database"
sudo -n runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='leadgen'" | grep -q 1 \
  || sudo -n runuser -u postgres -- psql -q -c "CREATE USER leadgen WITH PASSWORD 'leadgen'"
sudo -n runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='leadgen'" | grep -q 1 \
  || sudo -n runuser -u postgres -- createdb -O leadgen leadgen
(cd apps/api && .venv/bin/alembic upgrade head >"$LOGS/migrate.log" 2>&1) || fail "Database migration failed" migrate.log
sleep 1
apps/api/.venv/bin/python scripts/seed.py >"$LOGS/seed.log" 2>&1 || fail "Seeding failed" seed.log

step "Starting the API and background worker"
(cd apps/api && bg api .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000)
bg worker apps/api/.venv/bin/celery -A workers.celery_app.celery_app worker --loglevel=info --concurrency=2
bg beat apps/api/.venv/bin/celery -A workers.celery_app.celery_app beat --loglevel=info --schedule /tmp/leadgen-celerybeat
for _ in $(seq 1 60); do curl -sf http://localhost:8000/health >/dev/null && break; sleep 1; done
curl -sf http://localhost:8000/health >/dev/null || fail "API did not start" api.log

step "Building and starting the web app (takes about a minute)"
(cd apps/web && npx next build >"$LOGS/web-build.log" 2>&1) || fail "Web build failed" web-build.log
(cd apps/web && bg web npx next start --port 3000 --hostname 0.0.0.0)
for _ in $(seq 1 60); do curl -sf -o /dev/null http://localhost:3000/login && break; sleep 1; done
curl -sf -o /dev/null http://localhost:3000/login || fail "Web app did not start" web.log

CLAUDE_STATUS="not installed (run: curl -fsSL https://claude.ai/install.sh | bash)"
if command -v claude >/dev/null; then
  if [ -n "$(grep -s '^CLAUDE_CODE_OAUTH_TOKEN=.' .env)" ] || claude auth status >/dev/null 2>&1; then
    CLAUDE_STATUS="ready"
  else
    CLAUDE_STATUS="installed, not logged in -> run: claude setup-token  (then add CLAUDE_CODE_OAUTH_TOKEN=... to .env)"
  fi
fi

cat <<MSG

============================================================
 Lead Generator is running
   App:        $APP_URL
               login: demo@leadgen.local / DemoPass123!
   Test inbox: $INBOX_URL
 Claude Code: $CLAUDE_STATUS
 Next: AI & settings -> provider "Claude Code (your Claude subscription)" -> Save
 Gmail OAuth redirect URI (add in Google Cloud Console):
   $GOOGLE_REDIRECT_URI
 Logs: $LOGS     Restart: bash .devcontainer/start.sh
============================================================
MSG

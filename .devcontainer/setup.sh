#!/usr/bin/env bash
# One-time install for Codespaces / dev containers (runs on "postCreate").
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Installing PostgreSQL and Redis"
if ! command -v psql >/dev/null || ! command -v redis-server >/dev/null; then
  # A broken third-party package source in the base image must not stop the install.
  sudo -n apt-get update -y || echo "(apt-get update reported errors; continuing)"
  sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql redis-server
fi

# Optional: only needed if SCRAPER_PROVIDER=browser (Google Maps is scraped with Apify by default).
if ! command -v chromium >/dev/null; then
  echo "==> Installing Chromium (optional, for browser-based scraping)"
  sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends chromium chromium-driver \
    || echo "(Chromium not installed; Apify scraping does not need it)"
fi

echo "==> Installing Mailpit (test inbox)"
if ! command -v mailpit >/dev/null; then
  arch="$(uname -m)"; [ "$arch" = "aarch64" ] && arch=arm64 || arch=amd64
  curl -fsSL "https://github.com/axllent/mailpit/releases/latest/download/mailpit-linux-${arch}.tar.gz" \
    | sudo -n tar -xz -C /usr/local/bin mailpit
fi

echo "==> Installing API dependencies"
[ -d apps/api/.venv ] || python3 -m venv apps/api/.venv
apps/api/.venv/bin/pip install -q --upgrade pip
apps/api/.venv/bin/pip install -q -r apps/api/requirements.txt

echo "==> Installing Chromium for website screenshots"
apps/api/.venv/bin/playwright install --with-deps chromium \
  || echo "(Screenshot browser not installed; audits will run without screenshots)"

echo "==> Installing Claude Code (website analysis on your Claude subscription)"
if ! command -v claude >/dev/null && [ ! -x "$HOME/.local/bin/claude" ]; then
  curl -fsSL https://claude.ai/install.sh | bash || echo "(Claude Code install failed; see https://code.claude.com/docs/en/setup)"
fi

echo "==> Installing web dependencies"
(cd apps/web && npm install --no-audit --no-fund)

touch apps/api/.venv/.setup-complete
echo "==> Setup complete"

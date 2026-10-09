"""Run the whole app on this computer with no Docker, Redis or database install.

    Windows:  start-windows.bat          (after setup-windows.bat)
    Any OS:   <api venv python> scripts/run_local.py [--rebuild-web] [--port 3000]

Starts an embedded PostgreSQL (pgserver, data in .localdata/), runs migrations and the seed, then the API
(with the automatic-audit poller running inside it) and the web app. Ctrl+C stops everything.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"
DATA = ROOT / ".localdata"
LOGS = DATA / "logs"
TUNNEL_CONFIG = DATA / "cloudflared" / "config.yml"
IS_WINDOWS = os.name == "nt"


def say(message: str) -> None:
    print(f"==> {message}", flush=True)


def fail(message: str, log: Path | None = None) -> None:
    print(f"\n!! {message}", flush=True)
    if log and log.exists():
        lines = log.read_text(errors="replace").splitlines()[-25:]
        print(f"---- last lines of {log} ----")
        print("\n".join(lines))
    sys.exit(1)


def load_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        # utf-8-sig: tolerate the byte-order mark Notepad / PowerShell may add
        for line in env_file.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def stable_secret() -> str:
    """One random SECRET_KEY per install; saved credentials are encrypted with a key derived from it."""
    path = DATA / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def clear_stale_postmaster_pid() -> None:
    """Remove postmaster.pid left by a PostgreSQL that was killed (window closed, PC restarted).

    It still says "ready" on its old port, so pgserver connects to nothing ("Connection refused") instead of
    starting the server. Windows reuses process numbers, so check for a live postgres.exe with that number
    and an open port, not just for any process with that number.
    """
    pid_file = DATA / "postgres" / "postmaster.pid"
    try:
        lines = pid_file.read_text(encoding="utf-8", errors="replace").splitlines()
        pid, port = int(lines[0]), int(lines[3])
    except (OSError, ValueError, IndexError):
        return
    alive = False
    try:
        found = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15
        ).stdout.lower()
        alive = "postgres" in found
    except (OSError, subprocess.SubprocessError):
        alive = True  # can't tell: leave it to pgserver
    if alive:
        return  # a real PostgreSQL owns it (maybe still recovering): never take its lock away
    try:
        socket.create_connection(("127.0.0.1", port), timeout=3).close()
        return  # something answers on its port: leave it to pgserver
    except OSError:
        pass
    say("Clearing a leftover database lock from the last unclean stop")
    try:
        pid_file.unlink()
    except OSError:
        pass


def start_database():
    try:
        import pgserver
    except ImportError:
        fail("pgserver is not installed. Run the setup first (setup-windows.bat or: pip install pgserver).")
    say("Starting the database (embedded PostgreSQL)")
    # After the window was closed without Ctrl+C, PostgreSQL recovers on the next start, which takes ~40 s on Windows
    # (it waits on its own open log file). pgserver gives up after 10 s (TimeoutExpired) and then refuses a server that
    # is still "starting" (AssertionError), so keep retrying until recovery finishes.
    # A failed attempt leaves a half-made handle in pgserver's cache, which would be handed back on the next try:
    # drop it every time, and only accept a server that reports itself ready.
    from pgserver.postgres_server import PostgresServer

    clear_stale_postmaster_pid()
    deadline = time.time() + 180
    while True:
        try:
            server = pgserver.get_server(str(DATA / "postgres"), cleanup_mode="stop")
            info = server.get_postmaster_info()
            if info.is_running() and info.status == "ready":
                server.psql("SELECT 1;")  # really answering, not just a leftover "ready" in postmaster.pid
                break
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, AssertionError):
            clear_stale_postmaster_pid()
        PostgresServer._instances.clear()
        if time.time() > deadline:
            fail(f"The database did not start. See {DATA / 'postgres' / 'log'}")
        say("The database is recovering from the last unclean stop (up to a minute), waiting...")
        time.sleep(5)
    exists = server.psql("SELECT 1 FROM pg_database WHERE datname = 'leadgen';")
    if "1 row" not in exists:
        server.psql("CREATE DATABASE leadgen;")
    uri = server.get_uri("leadgen")
    return server, uri.replace("postgresql://", "postgresql+psycopg://", 1)


def run_step(name: str, cmd: list[str], cwd: Path, env: dict, log_name: str) -> None:
    log = LOGS / log_name
    with log.open("w", encoding="utf-8") as out:
        code = subprocess.call(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    if code != 0:
        fail(f"{name} failed", log)


def start_process(cmd: list[str], cwd: Path, env: dict, log_name: str) -> subprocess.Popen:
    out = (LOGS / log_name).open("w", encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, **kwargs)


def wait_for(url: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return True
        except Exception:
            time.sleep(1)
    return False


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def web_needs_build(force: bool) -> bool:
    marker = DATA / "web_build_stamp"
    sources = [WEB / "app", WEB / "components", WEB / "lib", WEB / "next.config.ts", WEB / "package.json"]
    newest = max(
        (f.stat().st_mtime for src in sources for f in ([src] if src.is_file() else src.rglob("*")) if f.is_file()),
        default=0,
    )
    if force or not (WEB / ".next" / "BUILD_ID").exists() or not marker.exists():
        return True
    return newest > marker.stat().st_mtime


def claude_code_status() -> str:
    configured = os.environ.get("CLAUDE_CODE_BIN") or load_dotenv().get("CLAUDE_CODE_BIN") or "claude"
    binary = shutil.which(configured) or shutil.which(str(Path.home() / ".local" / "bin" / "claude"))
    if not binary:
        return "not installed (run the setup again)"
    if load_dotenv().get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "ready (token in .env)"
    try:
        ok = subprocess.call([binary, "auth", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30) == 0
    except Exception:
        ok = False
    return "ready" if ok else "installed, not logged in -> run: claude setup-token, then add CLAUDE_CODE_OAUTH_TOKEN=... to .env"


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for folder in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        candidate = Path(folder) / "cloudflared" / "cloudflared.exe"
        if folder and candidate.exists():
            return str(candidate)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=3000, help="web app port (default 3000)")
    parser.add_argument("--api-port", type=int, default=8000, help="API port (default 8000)")
    parser.add_argument("--rebuild-web", action="store_true", help="rebuild the web app even if nothing changed")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm")
    if not npm:
        fail("Node.js (npm) was not found. Run the setup first.")

    server, database_url = start_database()
    app_url = f"http://localhost:{args.port}"
    api_url = f"http://127.0.0.1:{args.api_port}"
    env = os.environ.copy()
    env.update(load_dotenv())
    secret_key = env.get("SECRET_KEY", "")
    if not secret_key or secret_key.startswith("change-me"):
        secret_key = stable_secret()
    env.update(
        {
            "ENVIRONMENT": env.get("ENVIRONMENT", "development"),
            "SECRET_KEY": secret_key,
            "DATABASE_URL": database_url,
            "FRONTEND_URL": app_url,
            "BACKEND_URL": api_url,
            "GOOGLE_REDIRECT_URI": f"{app_url}/api/email-accounts/gmail/callback",
            "NEXT_PUBLIC_API_URL": api_url,
            "API_INTERNAL_URL": api_url,
            "INLINE_SCHEDULER": "true",
            # No Redis here: point Celery at a closed port so tasks run in background threads immediately.
            "REDIS_URL": "redis://127.0.0.1:1/0",
            "CELERY_BROKER_URL": "redis://127.0.0.1:1/0",
            "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/1",
            "PYTHONPATH": os.pathsep.join([str(API), str(ROOT)]),
            "PYTHONUTF8": "1",
        }
    )
    env.pop("ANTHROPIC_API_KEY", None)

    say("Preparing the database")
    run_step("Database migration", [sys.executable, "-m", "alembic", "upgrade", "head"], API, env, "migrate.log")
    run_step("Seeding", [sys.executable, str(ROOT / "scripts" / "seed.py")], ROOT, env, "seed.log")

    processes: list[subprocess.Popen] = []

    def shutdown(*_args) -> None:
        print("\n==> Stopping...", flush=True)
        for proc in processes:
            stop_process(proc)
        try:
            server.cleanup()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    say("Starting the API (with the automatic audit poller)")
    processes.append(
        start_process(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.api_port)],
            API,
            env,
            "api.log",
        )
    )
    if not wait_for(f"{api_url}/health", 90):
        shutdown_logs = LOGS / "api.log"
        for proc in processes:
            stop_process(proc)
        fail("The API did not start", shutdown_logs)

    if web_needs_build(args.rebuild_web):
        say("Building the web app (first time takes a few minutes)")
        run_step("Web build", [npm, "run", "build"], WEB, env, "web-build.log")
        (DATA / "web_build_stamp").write_text(str(time.time()), encoding="utf-8")
    say("Starting the web app")
    processes.append(start_process([npm, "run", "start", "--", "--port", str(args.port)], WEB, env, "web.log"))
    if not wait_for(f"{app_url}/login", 120):
        for proc in processes:
            stop_process(proc)
        fail("The web app did not start", LOGS / "web.log")

    print(
        f"""
============================================================
 Lead Generator is running
   App:   {app_url}
          login: demo@leadgen.local / DemoPass123!
 Claude Code: {claude_code_status()}
 Automatic audits run inside this window while it stays open.
 Logs: {LOGS}
 Stop: press Ctrl+C (or close this window)
============================================================
""",
        flush=True,
    )
    names = ["API", "web app"]
    public = env.get("PUBLIC_URL", "").strip()
    if TUNNEL_CONFIG.exists():
        cloudflared = find_cloudflared()
        if cloudflared:
            say("Starting the Cloudflare tunnel (email open tracking + unsubscribe links)")
            processes.append(
                start_process([cloudflared, "tunnel", "--config", str(TUNNEL_CONFIG), "run"], ROOT, env, "tunnel.log")
            )
            names.append("Cloudflare tunnel")
            if public and not wait_for(f"{public}/t/o/check.gif", 45):
                print(
                    f"   (Couldn't reach {public} from this PC yet. Usually Windows' DNS cache after a new record: "
                    f"run ipconfig /flushdns. Tunnel log: {LOGS / 'tunnel.log'})",
                    flush=True,
                )
        else:
            print("!! cloudflared is not installed, so opens aren't tracked. Run: winget install Cloudflare.cloudflared")
    print(f" Email tracking: {'on via ' + public if public and TUNNEL_CONFIG.exists() else 'off (no PUBLIC_URL / tunnel)'}", flush=True)
    if not args.no_browser:
        webbrowser.open(app_url)

    while True:
        for proc, name in zip(processes, names):
            if proc.poll() is not None:
                print(f"!! The {name} stopped unexpectedly. See {LOGS}", flush=True)
                shutdown()
        time.sleep(2)


if __name__ == "__main__":
    main()

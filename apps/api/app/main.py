import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
# Repo root (for `workers`). In Docker the API lives at /app, which has no grandparent.
ROOT = API_ROOT.parents[1] if len(API_ROOT.parents) > 1 else API_ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import engine
from app.logging_config import configure_logging, get_logger
from app.routers import analytics, audits, auth, campaigns, conversations, email_accounts, leads, scraping, templates, tracking
from app.routers import settings as settings_router
from app.routers.analytics import dashboard_router
from app.routers.misc import router_audit, router_logs, router_public, router_suppression

configure_logging("api")
# Some routers/ISPs fail to resolve many business domains; fall back to public DNS-over-HTTPS when they do.
from app.utils.dns_fallback import install as install_dns_fallback  # noqa: E402

install_dns_fallback()
logger = get_logger("api")
app_settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])

app = FastAPI(title="Lead Generator API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=app_settings.api_prefix)
app.include_router(leads.router, prefix=app_settings.api_prefix)
app.include_router(audits.router, prefix=app_settings.api_prefix)
app.include_router(scraping.router, prefix=app_settings.api_prefix)
app.include_router(templates.router, prefix=f"{app_settings.api_prefix}/templates")
app.include_router(templates.router, prefix=f"{app_settings.api_prefix}/email-templates")
app.include_router(campaigns.router, prefix=app_settings.api_prefix)
app.include_router(email_accounts.router, prefix=app_settings.api_prefix)
app.include_router(conversations.router, prefix=app_settings.api_prefix)
app.include_router(settings_router.router, prefix=app_settings.api_prefix)
app.include_router(analytics.router, prefix=app_settings.api_prefix)
app.include_router(dashboard_router, prefix=app_settings.api_prefix)
app.include_router(router_suppression, prefix=app_settings.api_prefix)
app.include_router(router_logs, prefix=app_settings.api_prefix)
app.include_router(router_audit, prefix=app_settings.api_prefix)
app.include_router(router_public, prefix=app_settings.api_prefix)
# Public /t/ routes (open tracking, unsubscribe): the only paths the tunnel exposes to the internet
app.include_router(tracking.router)


def _start_scheduler() -> None:
    from app.database import get_session
    from app.services.deliverability import load_day_start_hour
    from app.services.scheduler import start_inline_scheduler

    db = get_session()
    try:
        load_day_start_hour(db)  # when daily limits reset (AI & settings -> Sending)
    except Exception:
        pass  # before the first migration: midnight
    finally:
        db.close()
    start_inline_scheduler()


def _stop_scheduler() -> None:
    from app.services.scheduler import stop_inline_scheduler

    stop_inline_scheduler()


# Only active with INLINE_SCHEDULER=true (no Redis/Celery, e.g. scripts/run_local.py on Windows).
app.router.on_startup.append(_start_scheduler)
app.router.on_shutdown.append(_stop_scheduler)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(StarletteHTTPException)
async def starlette_http_error(_request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.error("unhandled_error", path=str(request.url), error=str(exc))
    if isinstance(exc, OperationalError):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Database is unavailable. Resume the Supabase project (or start local Postgres) and try again."
            },
        )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health():
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        logger.error("health_db_failed", error=str(exc))
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "ai_configured": app_settings.ai_configured,
        "google_oauth_configured": app_settings.google_oauth_configured,
        "apify_configured": app_settings.apify_configured,
    }


@app.get("/")
def root():
    return {"name": "Lead Generator API", "docs": "/docs"}

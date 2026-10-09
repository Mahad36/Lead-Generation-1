"""Public routes reached from recipients' mail apps: open-tracking image and unsubscribe.

Mounted at /t (outside API_PREFIX) so a tunnel can expose only this path to the internet.
"""

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.logging_config import get_logger
from app.services.suppression import add_suppression
from app.services.tracking import PIXEL_GIF, click_target, message_id_from_token, record_click, record_open
from app.utils.normalize import normalize_email
from app.utils.security import try_decode_token

logger = get_logger("tracking")
router = APIRouter(prefix="/t", tags=["tracking"], include_in_schema=False)

NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}


def _client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")


@router.get("/o/{token}.gif")
def open_pixel(token: str, request: Request, db: Session = Depends(get_db)):
    message_id = message_id_from_token(token)
    if message_id:
        try:
            record_open(db, message_id, user_agent=request.headers.get("user-agent", ""), ip=_client_ip(request))
        except Exception as exc:  # never show a broken image because of a tracking problem
            db.rollback()
            logger.warning("open_record_failed", error=str(exc))
    return Response(PIXEL_GIF, media_type="image/gif", headers=NO_CACHE)


@router.get("/c/{token}")
def click_redirect(token: str, request: Request, db: Session = Depends(get_db)):
    target = click_target(token)
    if not target:
        return _page("Link not found", "<p>This link is invalid.</p>", 404)
    message_id, url = target
    try:
        record_click(db, message_id, url, user_agent=request.headers.get("user-agent", ""), ip=_client_ip(request))
    except Exception as exc:  # the visitor must always reach the page
        db.rollback()
        logger.warning("click_record_failed", error=str(exc))
    return RedirectResponse(url, status_code=302, headers=NO_CACHE)


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f6f6f4;color:#222;display:flex;min-height:100vh;
align-items:center;justify-content:center;margin:0;padding:16px}}main{{background:#fff;border:1px solid #ddd;
border-radius:10px;padding:28px;max-width:420px}}h1{{font-size:20px;margin:0 0 12px}}p{{color:#555;line-height:1.5}}
button{{background:#222;color:#fff;border:0;border-radius:6px;padding:10px 18px;font-size:15px;cursor:pointer}}</style>
</head><body><main><h1>{escape(title)}</h1>{body}</main></body></html>"""
    return HTMLResponse(html, status_code=status_code, headers=NO_CACHE)


def _unsubscribe_target(token: str) -> tuple[str, str] | None:
    payload = try_decode_token(token)
    if not payload or payload.get("type") != "unsubscribe":
        return None
    email, user_id = normalize_email(payload.get("email") or ""), payload.get("sub") or ""
    return (user_id, email) if email and user_id else None


@router.get("/u/{token}")
def unsubscribe_page(token: str):
    # A GET never unsubscribes: link scanners open every link in an email.
    if not _unsubscribe_target(token):
        return _page("Link expired", "<p>This unsubscribe link is invalid or has expired.</p>", 400)
    return _page(
        "Unsubscribe",
        '<p>You will not receive further emails from this sender.</p>'
        '<form method="post"><button type="submit">Unsubscribe</button></form>',
    )


@router.post("/u/{token}")
def unsubscribe_confirm(token: str, db: Session = Depends(get_db)):
    # Also handles RFC 8058 one-click unsubscribe (List-Unsubscribe-Post) from Gmail / Apple Mail.
    target = _unsubscribe_target(token)
    if not target:
        return _page("Link expired", "<p>This unsubscribe link is invalid or has expired.</p>", 400)
    user_id, email = target
    add_suppression(db, user_id, email, reason="user_unsubscribed", source="unsubscribe_link")
    logger.info("unsubscribed", user_id=user_id)
    return _page("You're unsubscribed", "<p>You won't receive further emails from this sender.</p>")

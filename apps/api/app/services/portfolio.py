"""Reference sites from the user's portfolio Google Sheet, matched to each lead's niche.

The sheet (shared "Anyone with the link: Viewer") is read through Google's CSV export. Two layouts work:
  - one column per niche, with the sites listed under each heading (the Bee Creative sheet), or
  - one row per site, with a URL column and a niche column (any header containing "url"/"website" and "niche").
Notes after a URL such as "(SquareSpace)" or "(Wix)" become the site's platform. Every site is checked in the
background so a dead or parked site is never shown to a lead. The sheet is re-read every hour.

Claude picks the reference sites while writing the audit (see audit_ai: reference_sites); only URLs that exist in
the sheet are kept.
"""

from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.logging_config import get_logger

logger = get_logger("portfolio")

SHEET_TTL_SECONDS = 3600
ALIVE_TTL_SECONDS = 24 * 3600
PER_NICHE_IN_PROMPT = 6
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
PARKED_MARKERS = ("domain is for sale", "buy this domain", "this domain may be for sale", "parked free", "godaddy.com/forsale")

_lock = threading.Lock()
_sheets: dict[str, tuple[float, list[dict]]] = {}  # sheet url -> (loaded at, sites)
_checking: set[str] = set()


def _cache_dir() -> Path:
    folder = Path(tempfile.gettempdir()) / "leadgen-portfolio"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def csv_export_url(sheet_url: str) -> str:
    """Google Sheets share/edit link -> CSV export link (keeps the tab, gid, if the link has one)."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", sheet_url or "")
    if not match:
        return sheet_url
    gid = re.search(r"[#&?]gid=(\d+)", sheet_url)
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv" + (
        f"&gid={gid.group(1)}" if gid else ""
    )


def _site(raw: str, niche: str) -> dict | None:
    """'https://nydrconnect.com/ (SquareSpace)' -> {url, domain, niche, platform}."""
    text = (raw or "").strip()
    if not text or "." not in text:
        return None
    platform = ""
    note = re.search(r"\(([^)]*)\)", text)
    if note:
        platform = note.group(1).strip()
        text = text[: note.start()].strip()
    text = text.split()[0].rstrip("/")
    url = text if re.match(r"^https?://", text, re.I) else f"https://{text}"
    domain = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not domain or "." not in domain:
        return None
    return {"url": url, "domain": domain, "niche": niche.strip(), "platform": platform}


def parse_sheet(text: str) -> list[dict]:
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    lower = [h.lower() for h in header]
    url_col = next((i for i, h in enumerate(lower) if "url" in h or "website" in h or h == "site"), None)
    niche_col = next((i for i, h in enumerate(lower) if "niche" in h or "industry" in h or "category" in h), None)
    sites: list[dict] = []
    if url_col is not None and niche_col is not None:  # one row per site
        for row in rows[1:]:
            if url_col < len(row) and (site := _site(row[url_col], row[niche_col] if niche_col < len(row) else "")):
                sites.append(site)
    else:  # one column per niche
        for col, niche in enumerate(header):
            for row in rows[1:]:
                if col < len(row) and (site := _site(row[col], niche)):
                    sites.append(site)
    seen: set[str] = set()
    return [s for s in sites if not (s["domain"] in seen or seen.add(s["domain"]))]


def _alive_file(sheet_url: str) -> Path:
    key = re.sub(r"[^a-zA-Z0-9]", "", csv_export_url(sheet_url))[-60:]
    return _cache_dir() / f"alive-{key}.json"


def _load_alive(sheet_url: str) -> dict:
    try:
        return json.loads(_alive_file(sheet_url).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _site_is_alive(url: str) -> bool:
    try:
        response = httpx.get(url, headers={"User-Agent": BROWSER_UA}, timeout=12, follow_redirects=True)
    except httpx.HTTPError:
        return False
    if response.status_code >= 500 or response.status_code in (404, 410):
        return False
    head = response.text[:6000].lower() if response.status_code < 400 else ""
    return not any(marker in head for marker in PARKED_MARKERS)


def _check_alive(sheet_url: str, sites: list[dict]) -> None:
    """Background: which sites still load (results cached for a day)."""
    key = csv_export_url(sheet_url)
    if key in _checking:
        return
    _checking.add(key)

    def run() -> None:
        try:
            known = _load_alive(sheet_url)
            now = time.time()
            due = [s for s in sites if now - (known.get(s["domain"]) or {}).get("at", 0) > ALIVE_TTL_SECONDS]
            with ThreadPoolExecutor(max_workers=10) as pool:
                for site, alive in zip(due, pool.map(lambda s: _site_is_alive(s["url"]), due)):
                    known[site["domain"]] = {"alive": alive, "at": now}
            _alive_file(sheet_url).write_text(json.dumps(known), encoding="utf-8")
            logger.info("portfolio_checked", sites=len(sites), checked=len(due),
                        dead=sum(1 for v in known.values() if not v.get("alive")))
        except Exception as exc:
            logger.warning("portfolio_check_failed", error=str(exc))
        finally:
            _checking.discard(key)

    threading.Thread(target=run, name="portfolio-check", daemon=True).start()


def load(sheet_url: str, *, force: bool = False) -> list[dict]:
    """Sites from the sheet (cached for an hour), each with "alive": True / False / None (not checked yet)."""
    if not (sheet_url or "").strip():
        return []
    key = csv_export_url(sheet_url)
    with _lock:
        cached = _sheets.get(key)
    if cached and not force and time.time() - cached[0] < SHEET_TTL_SECONDS:
        sites = cached[1]
    else:
        response = httpx.get(key, timeout=20, follow_redirects=True)
        if response.status_code != 200 or "text/csv" not in response.headers.get("content-type", ""):
            raise ValueError(
                "Couldn't read the Google Sheet. In Google Sheets click Share and set General access to "
                "'Anyone with the link' (Viewer)."
            )
        sites = parse_sheet(response.content.decode("utf-8", errors="replace"))
        with _lock:
            _sheets[key] = (time.time(), sites)
        _check_alive(sheet_url, sites)
    alive = _load_alive(sheet_url)
    return [s | {"alive": (alive.get(s["domain"]) or {}).get("alive")} for s in sites]


def sheet_url_for(db: Session, user_id: str) -> str:
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    return (settings.portfolio_sheet_url if settings else "") or ""


def for_prompt(db: Session, user_id: str) -> dict[str, list[str]]:
    """{niche: [urls]} of live sites for Claude to choose from (a few per niche keeps the prompt small)."""
    try:
        sites = load(sheet_url_for(db, user_id))
    except Exception as exc:  # outreach goes on without reference sites
        logger.warning("portfolio_load_failed", error=str(exc))
        return {}
    grouped: dict[str, list[str]] = {}
    for site in sites:
        if site["alive"] is False:
            continue
        bucket = grouped.setdefault(site["niche"] or "Other", [])
        if len(bucket) < PER_NICHE_IN_PROMPT:
            bucket.append(site["url"] + (f" ({site['platform']})" if site["platform"] else ""))
    return grouped


def keep_known(chosen: list[dict], portfolio: dict[str, list[str]]) -> list[dict]:
    """Drop any reference site Claude suggested that isn't in the sheet (by domain)."""
    known = {}
    for niche, urls in portfolio.items():
        for entry in urls:
            url = entry.split(" (")[0]
            known[(urlparse(url).hostname or "").lower().removeprefix("www.")] = (url, niche)
    kept = []
    for item in chosen:
        raw = str(item.get("url") or "").strip()
        domain = (urlparse(raw if "://" in raw else f"https://{raw}").hostname or "").lower().removeprefix("www.")
        if domain in known and all(k["url"] != known[domain][0] for k in kept):
            kept.append({"url": known[domain][0], "niche": known[domain][1], "note": str(item.get("note") or "")[:300]})
    return kept[:3]


def status(db: Session, user_id: str, *, force: bool = False) -> dict:
    url = sheet_url_for(db, user_id)
    if not url:
        return {"configured": False}
    try:
        sites = load(url, force=force)
    except Exception as exc:
        return {"configured": True, "error": str(exc)}
    return {
        "configured": True,
        "sites": len(sites),
        "niches": len({s["niche"] for s in sites}),
        "alive": sum(1 for s in sites if s["alive"] is True),
        "dead": sum(1 for s in sites if s["alive"] is False),
        "checking": sum(1 for s in sites if s["alive"] is None),
    }

"""Google Maps lead search via Apify (no local Chrome)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import get_settings
from app.logging_config import get_logger
from app.services.scraper import (
    ControlCallback,
    ProgressCallback,
    SaveCallback,
    ScrapedBusiness,
    ScrapingCancelledError,
    build_maps_query,
)

logger = get_logger("apify_maps")

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "compass~crawler-google-places"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(RuntimeError):
    pass


APIFY_TOKEN_MISSING = "Add your Apify API token in AI & settings → Google Maps (Apify), then run the scrape again."


def apify_token() -> str:
    return (get_settings().apify_token or "").strip()


def apify_configured() -> bool:
    return bool(apify_token())


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _request_error(response: httpx.Response) -> ApifyError:
    detail = (response.text or "")[:240]
    if response.status_code in {401, 403}:
        return ApifyError("Apify rejected the API token. Check it in AI & settings → Google Maps (Apify).")
    return ApifyError(f"Apify request failed ({response.status_code}): {detail}")


def _first_email(item: dict[str, Any]) -> str:
    for key in ("emails", "email", "emailsUncertain", "contactEmails"):
        value = item.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip()
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and "@" in entry:
                    return entry.strip()
                if isinstance(entry, dict):
                    email = str(entry.get("email") or entry.get("value") or "").strip()
                    if "@" in email:
                        return email
    return ""


def _contact_name(item: dict[str, Any]) -> str:
    """A person's name when Apify returns one (leads enrichment / contact details); usually empty for Maps data."""
    for key in ("leadsEnrichment", "contacts", "people"):
        entries = item.get(key)
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("fullName") or entry.get("name") or "").strip()
                if not name:
                    name = f"{entry.get('firstName') or ''} {entry.get('lastName') or ''}".strip()
                if name:
                    return name[:255]
    for key in ("contactName", "ownerName"):
        if isinstance(item.get(key), str) and item[key].strip():
            return item[key].strip()[:255]
    return ""


def apify_item_to_business(item: dict[str, Any], category: str) -> ScrapedBusiness | None:
    name = str(item.get("title") or item.get("name") or "").strip()
    if not name:
        return None
    extra = {"source": "apify"}
    email = _first_email(item)
    if email:
        extra["email"] = email
    contact = _contact_name(item)
    if contact:
        extra["contact_name"] = contact
    rating = item.get("totalScore")
    try:
        rating_val = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_val = None
    reviews = item.get("reviewsCount")
    try:
        review_val = int(reviews) if reviews is not None else None
    except (TypeError, ValueError):
        review_val = None
    return ScrapedBusiness(
        business_name=name,
        category=str(item.get("categoryName") or item.get("category") or category or "").strip(),
        address=str(item.get("address") or "").strip(),
        phone=str(item.get("phoneUnformatted") or item.get("phone") or "").strip(),
        website=str(item.get("website") or "").strip(),
        google_maps_url=str(item.get("url") or item.get("googleMapsUrl") or "").strip(),
        google_place_id=str(item.get("placeId") or "").strip(),
        rating=rating_val,
        review_count=review_val,
        extra=extra,
    )


def _start_run(token: str, payload: dict) -> dict:
    actor = get_settings().apify_actor_id or DEFAULT_ACTOR
    response = httpx.post(
        f"{APIFY_BASE}/acts/{actor}/runs",
        headers=_headers(token),
        json=payload,
        timeout=60,
    )
    if response.status_code >= 400:
        raise _request_error(response)
    return response.json()["data"]


def _wait_for_run(token: str, run_id: str, control: ControlCallback | None, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if control:
            state = control()
            if state == "cancelled":
                raise ScrapingCancelledError("Scraping cancelled")
            while state == "paused":
                time.sleep(2)
                state = control()
                if state == "cancelled":
                    raise ScrapingCancelledError("Scraping cancelled")
        response = httpx.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            headers=_headers(token),
            timeout=30,
        )
        if response.status_code >= 400:
            raise _request_error(response)
        data = response.json()["data"]
        status = data.get("status") or ""
        if status in TERMINAL:
            if status != "SUCCEEDED":
                raise ApifyError(f"Apify run {status.lower().replace('-', ' ')}")
            return data
        time.sleep(5)
    raise ApifyError("Apify run timed out while waiting for Google Maps results")


def _fetch_items(token: str, dataset_id: str) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        response = httpx.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=_headers(token),
            params={"offset": offset, "limit": 250},
            timeout=60,
        )
        if response.status_code >= 400:
            raise _request_error(response)
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 250:
            break
        offset += 250
    return items


def scrape_google_maps_apify(
    *,
    country: str,
    city: str,
    category: str,
    max_results: int,
    save: SaveCallback,
    progress: ProgressCallback | None = None,
    control: ControlCallback | None = None,
    token: str | None = None,
) -> dict:
    token = (token or apify_token()).strip()
    if not token:
        raise ApifyError(APIFY_TOKEN_MISSING)
    query = build_maps_query(country, city, category)
    location = ", ".join(part for part in (city, country) if part)
    payload = {
        "searchStringsArray": [category or query],
        "locationQuery": location or query,
        "maxCrawledPlacesPerSearch": max(1, int(max_results or 10000)),
        "language": "en",
        "maxReviews": 0,
        "maxImages": 0,
        "scrapeContacts": True,
        "maximumLeadsEnrichmentRecords": 0,
    }
    found = saved = errors = 0

    def emit(status: str) -> None:
        if progress:
            progress({"query": query, "status": status, "found": found, "saved": saved, "errors": errors, "page": 1})

    emit("starting Apify")
    logger.info("apify_run_started", query=query, location=location)
    run = _start_run(token, payload)
    emit("waiting for Apify")
    finished = _wait_for_run(token, run["id"], control)
    dataset_id = finished.get("defaultDatasetId") or run.get("defaultDatasetId")
    if not dataset_id:
        raise ApifyError("Apify finished without a results dataset")
    emit("saving leads")
    for item in _fetch_items(token, dataset_id):
        if control and control() == "cancelled":
            raise ScrapingCancelledError("Scraping cancelled")
        if not isinstance(item, dict):
            continue
        business = apify_item_to_business(item, category)
        if not business:
            errors += 1
            continue
        found += 1
        try:
            save(business)
            saved += 1
            emit("extracting")
        except Exception as exc:
            errors += 1
            logger.warning("apify_save_failed", name=business.business_name, error=str(exc))
    emit("completed")
    return {"found": found, "saved": saved, "errors": errors, "query": query}

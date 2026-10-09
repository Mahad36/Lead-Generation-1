"""Built-in Google Maps scraper (no Apify account needed).

Fast path: one headless Chromium (Playwright, images/fonts blocked) opens the Maps search and scrolls the results
list. Each result card already carries the name, rating, reviews, category, address, opening hours, phone and website,
so for most searches no place page is opened. Compact cards (e.g. salons) show no phone / website; for those the
business's own Maps page is opened, a few tabs at a time. Meanwhile every business website is crawled in parallel (the
app's own crawler: homepage + contact/about pages) for email addresses, extra phones and social links. Each business is
handed to `save` as soon as its checks finish.

Stops safely on CAPTCHA / "unusual traffic" pages; it does not try to get around Google's protections.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, quote_plus, urlparse

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("scraper")


class ScrapingBlockedError(Exception):
    pass


class ScrapingCancelledError(Exception):
    pass


@dataclass
class ScrapedBusiness:
    business_name: str
    category: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    google_maps_url: str = ""
    google_place_id: str = ""
    rating: float | None = None
    review_count: int | None = None
    extra: dict = field(default_factory=dict)


ProgressCallback = Callable[[dict], None]
SaveCallback = Callable[[ScrapedBusiness], None]
ControlCallback = Callable[[], str]

CAPTCHA_MARKERS = (
    "unusual traffic",
    "sorry/index",
    "detected unusual traffic",
    "our systems have detected",
)
END_OF_LIST = "reached the end of the list"
# Many small-business sites (and their firewalls) refuse unknown bots, so websites are checked as a normal browser.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
BLOCKED_RESOURCES = {"image", "media", "font"}
HOURS_PREFIXES = ("open", "closed", "opens", "closes", "temporarily closed", "permanently closed", "hours")

# Every result card in one pass: the visible text plus the bits that live in attributes.
CARDS_JS = """
() => [...document.querySelectorAll("div[role='feed'] a.hfpxzc")].map((a) => {
  const card = a.closest("div[jsaction]")?.parentElement || a.parentElement;
  const website = card?.querySelector("a[data-value='Website'], a[aria-label^='Visit'][href^='http']");
  const stars = card?.querySelector("span[role='img'][aria-label]");
  const booking = [...(card?.querySelectorAll("a[href^='http']") || [])].find(
    (x) => x !== a && x !== website && !x.href.includes("google.")
  );
  return {
    name: a.getAttribute("aria-label") || "",
    href: a.href || "",
    text: card?.innerText || "",
    website: website?.href || "",
    stars: stars?.getAttribute("aria-label") || "",
    booking: booking?.href || "",
  };
})
"""


def build_maps_query(country: str, city: str, category: str) -> str:
    return f"{category} in {city}, {country}"


def short_browser_error(exc: Exception) -> str:
    text = str(exc).split("Call log:")[0].split("Stacktrace:")[0].strip()
    if "timed out receiving message from renderer" in text.lower() or "Timeout" in text:
        return "Google Maps took too long to load. Retry the scrape; a fresh browser will be used."
    if "Executable doesn't exist" in text:
        return "The scraper browser is not installed. Run setup-windows.bat again (or: playwright install chromium)."
    first = text.splitlines()[0].strip() if text else ""
    return first[:240] or "The browser failed while opening Google Maps."


# ---------------------------------------------------------------- parsing helpers


def _clean_maps_phone(raw: str) -> str:
    value = re.sub(r"^(phone|tel|call)[:\s]+", "", (raw or "").strip(), flags=re.I)
    value = value.replace("tel:", "")
    value = re.sub(r"[^\d+().\s-]", "", value).strip()
    digits = re.sub(r"\D", "", value)
    return value if len(digits) >= 8 else ""


def _clean_maps_website(raw: str) -> str:
    value = re.sub(r"^(website|web)[:\s]+", "", (raw or "").strip(), flags=re.I)
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not re.match(r"^https?://", value, re.I):
        if "." in value and " " not in value:
            value = "https://" + value.lstrip("/")
        else:
            return ""
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:
        return ""
    if not host or "google." in host or host.endswith("gstatic.com"):
        return ""
    # Google wraps some links in /url?q=...; tracking parameters are dropped.
    return re.sub(r"[?&]utm_[^&#]*", "", value).rstrip("?&")


def _parse_rating(raw: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)", raw or "")
    return float(match.group(1).replace(",", ".")) if match else None


def _parse_reviews(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else None


def _place_id_from_url(url: str) -> str:
    if not url:
        return ""
    qs = parse_qs(urlparse(url).query)
    if "cid" in qs:
        return qs["cid"][0]
    match = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", url)
    return match.group(1) if match else ""


def _coordinates(url: str) -> tuple[float, float] | None:
    match = re.search(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", url or "")
    return (float(match.group(1)), float(match.group(2))) if match else None


_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_PRICE_RE = re.compile(r"^[$€£₹]{1,4}$|^[$€£₹][\d,]+")


def _is_icon(line: str) -> bool:
    return all(0xE000 <= ord(ch) <= 0xF8FF or ch.isspace() for ch in line)


def parse_card(card: dict, fallback_category: str) -> ScrapedBusiness | None:
    """Turn one result card (CARDS_JS) into a business. Pure function, so it is unit-testable."""
    name = (card.get("name") or "").strip()
    href = card.get("href") or ""
    if not name or "/maps/place" not in href:
        return None
    lines = [line.strip() for line in (card.get("text") or "").split("\n")]
    lines = [
        line
        for line in lines
        if line and line != name and not _is_icon(line) and line not in {"Website", "Directions", "Book online", "Order online", "Call"}
    ]
    rating = review_count = None
    stars = card.get("stars") or ""
    match = re.search(r"([\d.,]+)\s*stars?\s*([\d,.]+)\s*review", stars, re.I)
    if match:
        rating, review_count = _parse_rating(match.group(1)), _parse_reviews(match.group(2))
    category, address, hours, phone = "", "", "", ""
    for line in lines:
        if rating is None and re.fullmatch(r"\d[.,]\d(\s*\([\d,.]+\))?", line):
            rating = _parse_rating(line)
            counted = re.search(r"\(([\d,.]+)\)", line)
            review_count = _parse_reviews(counted.group(1)) if counted else review_count
            continue
        parts = [p.strip() for p in re.split(r"[·⋅]", line) if p.strip()]
        if not parts:
            continue
        if parts[0].lower().startswith(HOURS_PREFIXES):
            hours = hours or line.split("·")[0].strip()
            for part in parts[1:]:
                phone = phone or _clean_maps_phone(part)
            continue
        phone_parts = [p for p in parts if _PHONE_RE.fullmatch(p) and _clean_maps_phone(p)]
        if phone_parts:
            phone = phone or _clean_maps_phone(phone_parts[0])
            continue
        if not category:
            kept = [p for p in parts if not _PRICE_RE.match(p) and not re.fullmatch(r"\d[.,]\d", p) and not _is_icon(p)]
            if kept:
                category = kept[0]
                address = kept[-1] if len(kept) > 1 else ""
    extra: dict = {"source": "scraper"}
    if hours:
        extra["hours"] = hours
        if "permanently closed" in hours.lower():
            extra["permanently_closed"] = True
    coordinates = _coordinates(href)
    if coordinates:
        extra["latitude"], extra["longitude"] = coordinates
    booking = _clean_maps_website(card.get("booking") or "")
    if booking:
        extra["booking_url"] = booking
    return ScrapedBusiness(
        business_name=name,
        category=category or fallback_category,
        address=address,
        phone=phone,
        website=_clean_maps_website(card.get("website") or ""),
        google_maps_url=href.split("?")[0],
        google_place_id=_place_id_from_url(href),
        rating=rating,
        review_count=review_count,
        extra=extra,
    )


def website_contacts(url: str) -> dict:
    """Emails, phones and social profiles from a business website (homepage + contact/about pages)."""
    from app.services.crawler import crawl_website
    from app.utils.emails import choose_primary_email
    from app.utils.normalize import extract_domain

    settings = get_settings()
    result = None
    for attempt in range(2):  # one retry: DNS / connection hiccups are common when many sites are checked at once
        try:
            result = crawl_website(
                url,
                max_pages=settings.scraper_site_pages,
                timeout=settings.scraper_site_timeout_seconds,
                user_agent=BROWSER_UA,
            )
        except Exception as exc:  # one bad website never stops the scrape
            if attempt:
                return {"website_error": str(exc)[:160]}
            continue
        if result.homepage and result.homepage.status_code:
            break
        time.sleep(1)
    if result is None:
        return {"website_error": "The website could not be reached"}
    emails = list(dict.fromkeys(e["email"] for e in result.emails if e.get("email")))
    found: dict = {}
    if emails:
        found["email"] = choose_primary_email(emails, extract_domain(url)) or emails[0]
        found["emails"] = emails[:10]
    if result.phones:
        found["website_phones"] = list(dict.fromkeys(result.phones))[:5]
    if result.social:
        found["social"] = result.social
    if result.homepage and result.homepage.title:
        found["website_title"] = result.homepage.title[:200]
    if not emails:
        failed = [p.error for p in result.pages if p.error]
        if result.error or (failed and not (result.homepage and result.homepage.status_code)):
            found["website_error"] = (result.error or failed[0])[:160]
        elif result.homepage and result.homepage.status_code >= 400:
            found["website_error"] = f"The website answered HTTP {result.homepage.status_code}"
    return found


# ---------------------------------------------------------------- browser

PLACE_JS = r"""
() => {
  const item = (sel) => document.querySelector(sel);
  const phone = document.querySelector("[data-item-id^='phone:tel:']");
  const reviews = (document.querySelector("div.F7nice")?.innerText || "").match(/\(([\d.,]+)\)/);
  return {
    website: item("a[data-item-id='authority']")?.href || "",
    phone: phone ? phone.getAttribute("data-item-id").replace("phone:tel:", "") : "",
    address: (item("[data-item-id='address']")?.getAttribute("aria-label") || "").replace(/^Address:\s*/, "").trim(),
    reviews: reviews ? reviews[1] : "",
  };
}
"""
BLOCKED_MESSAGE = "Google Maps showed a CAPTCHA / unusual-traffic page. Try again later."


async def _blocked(page) -> bool:
    url = (page.url or "").lower()
    if "google.com/sorry" in url or "/sorry/index" in url:
        return True
    try:
        head = (await page.content() or "")[:8000].lower()
    except Exception:
        return False
    return any(marker in head for marker in CAPTCHA_MARKERS)


async def _accept_consent(page) -> None:
    """EU visitors get a cookie consent page first."""
    if "consent." not in (page.url or ""):
        return
    for selector in ("button[aria-label*='Accept']", "form[action*='consent'] button", "button:has-text('Accept all')"):
        try:
            await page.click(selector, timeout=3000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            return
        except Exception:
            continue


async def _place_details(context, business: ScrapedBusiness) -> None:
    """Open the business's own Maps page for what its result card didn't show (compact cards: no phone / website)."""
    page = await context.new_page()
    try:
        url = business.google_maps_url
        await page.goto(url + ("&" if "?" in url else "?") + "hl=en", wait_until="domcontentloaded", timeout=30_000)
        if await _blocked(page):
            raise ScrapingBlockedError(BLOCKED_MESSAGE)
        try:
            await page.wait_for_selector(
                "a[data-item-id='authority'], [data-item-id^='phone:tel:'], [data-item-id='address']", timeout=6000
            )
            await page.wait_for_timeout(400)  # the website / phone rows render right after the address
        except Exception:
            return
        data = await page.evaluate(PLACE_JS)
        business.website = business.website or _clean_maps_website(data.get("website") or "")
        business.phone = business.phone or _clean_maps_phone(data.get("phone") or "")
        business.address = data.get("address") or business.address
        business.review_count = business.review_count or _parse_reviews(data.get("reviews") or "")
        business.extra["place_page_checked"] = True
    finally:
        await page.close()


# Firewalls that refuse plain HTTP clients but often let a real browser in (Cloudflare checks are not bypassed).
BROWSER_RETRY_ERRORS = ("HTTP 403", "HTTP 429", "HTTP 503")
CHALLENGE_MARKERS = ("just a moment", "attention required", "cf-challenge", "challenge-platform")
CONTACT_LINK_JS = r"""
(host) => [...document.querySelectorAll("a[href]")]
  .map((a) => a.href)
  .find((href) => { try { const u = new URL(href); return u.hostname.endsWith(host) && /contact|about/i.test(u.pathname); }
                    catch { return false; } }) || ""
"""


def needs_browser(result: dict) -> bool:
    return not result.get("email") and any(code in (result.get("website_error") or "") for code in BROWSER_RETRY_ERRORS)


async def render_site_emails(context, url: str) -> dict:
    """Open a website (and its contact page) in a real browser tab and read the emails on it."""
    from app.services.email_extraction import extract_emails_from_html
    from app.utils.emails import choose_primary_email
    from app.utils.normalize import extract_domain

    page = await context.new_page()
    emails: list[str] = []
    try:
        for _ in range(2):  # homepage, then the contact / about page
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(1500)
            html = await page.content()
            if any(marker in html[:20000].lower() for marker in CHALLENGE_MARKERS):
                return {"website_error": "The website is protected by a bot check (Cloudflare)"}
            emails += [e["email"] for e in extract_emails_from_html(html, page.url) if e["email"] not in emails]
            if emails:
                break
            url = await page.evaluate(CONTACT_LINK_JS, extract_domain(url))
            if not url:
                break
    except Exception as exc:
        return {"website_error": f"Browser check failed: {str(exc).splitlines()[0][:120]}"}
    finally:
        await page.close()
    if not emails:
        return {}
    return {"email": choose_primary_email(emails, extract_domain(url)) or emails[0], "emails": emails[:10], "website_error": ""}


def scrape_google_maps(**kwargs) -> dict:
    """Synchronous entry point used by the scraping job (runs the async scraper on the calling thread)."""
    import asyncio

    return asyncio.run(_scrape(**kwargs))


async def _scrape(
    *,
    country: str,
    city: str,
    category: str,
    max_results: int,
    save: SaveCallback,
    progress: ProgressCallback | None = None,
    control: ControlCallback | None = None,
    headless: bool | None = None,
    **_unused,
) -> dict:
    import asyncio

    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    settings = get_settings()
    query = build_maps_query(country, city, category)
    url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
    found = saved = errors = 0
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    site_pool = ThreadPoolExecutor(max_workers=max(1, settings.scraper_site_workers), thread_name_prefix="site-check")
    tabs = asyncio.Semaphore(max(1, settings.scraper_place_tabs))
    tasks: set[asyncio.Task] = set()

    def emit(status: str, **extra) -> None:
        if progress:
            progress({"query": query, "status": status, "found": found, "saved": saved, "errors": errors, **extra})

    async def check_control() -> None:
        if not control:
            return
        state = control()
        while state == "paused":
            await asyncio.sleep(1.5)
            state = control()
        if state == "cancelled":
            raise ScrapingCancelledError("Scraping cancelled")

    async def enrich(context, business: ScrapedBusiness) -> ScrapedBusiness:
        """Place page (only if the card lacked phone / website), then the website check for emails."""
        if not business.website or not business.phone:
            async with tabs:
                try:
                    await _place_details(context, business)
                except ScrapingBlockedError:
                    raise
                except Exception as exc:  # the business is still saved with what the card showed
                    business.extra["place_page_error"] = str(exc)[:160]
        if business.website:
            try:
                business.extra.update(await loop.run_in_executor(site_pool, website_contacts, business.website))
            except Exception as exc:
                business.extra["website_error"] = str(exc)[:160]
            if needs_browser(business.extra):
                async with tabs:
                    business.extra.update(await render_site_emails(context, business.website))
            if not business.extra.get("website_error"):
                business.extra.pop("website_error", None)
        return business

    def save_finished() -> None:
        """Hand finished businesses to `save` on this thread (the job's database session isn't thread-safe)."""
        nonlocal saved
        for task in [t for t in tasks if t.done()]:
            tasks.discard(task)
            if task.cancelled():
                continue
            exc = task.exception()
            if isinstance(exc, ScrapingBlockedError):
                raise exc
            if exc is None:
                save(task.result())
                saved += 1

    logger.info("scrape_started", query=query)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=settings.headless if headless is None else headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = await browser.new_context(locale="en-US", viewport={"width": 1280, "height": 1000})

                async def block_heavy(route):
                    if route.request.resource_type in BLOCKED_RESOURCES:
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", block_heavy)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                await _accept_consent(page)
                if await _blocked(page):
                    raise ScrapingBlockedError(BLOCKED_MESSAGE)
                try:
                    await page.wait_for_selector("div[role='feed'] a.hfpxzc", timeout=15_000)
                except PlaywrightError:
                    if await _blocked(page):
                        raise ScrapingBlockedError(BLOCKED_MESSAGE)
                    logger.info("scrape_no_results", query=query)
                    emit("completed")
                    return {"found": 0, "saved": 0, "errors": 0, "query": query}

                seen: set[str] = set()
                stale_rounds = 0
                while found < max_results:
                    await check_control()
                    new_cards = [c for c in await page.evaluate(CARDS_JS) if c.get("href") and c["href"] not in seen]
                    for card in new_cards:
                        if found >= max_results:
                            break
                        seen.add(card["href"])
                        business = parse_card(card, category)
                        if not business:
                            errors += 1
                            continue
                        found += 1
                        tasks.add(asyncio.create_task(enrich(context, business)))
                    save_finished()
                    emit("extracting", page_size=len(seen), checking=len(tasks))
                    if found >= max_results:
                        break
                    feed_text = await page.inner_text("div[role='feed']") if new_cards else ""
                    if END_OF_LIST in feed_text.lower():
                        break
                    stale_rounds = 0 if new_cards else stale_rounds + 1
                    if stale_rounds >= 5:
                        break
                    await page.eval_on_selector("div[role='feed']", "feed => feed.scrollBy(0, feed.scrollHeight)")
                    await page.wait_for_timeout(int(settings.scrape_scroll_pause_seconds * 1000) + 300 * stale_rounds)
                    if await _blocked(page):
                        raise ScrapingBlockedError(BLOCKED_MESSAGE)

                # The list is complete; keep saving as place pages and website checks finish.
                while tasks:
                    await check_control()
                    await asyncio.wait(tasks, timeout=2, return_when=asyncio.FIRST_COMPLETED)
                    save_finished()
                    emit("checking_websites", checking=len(tasks))
            except ScrapingBlockedError:
                # Keep what was already finished before Google blocked.
                for task in [t for t in tasks if t.done() and not t.cancelled() and t.exception() is None]:
                    save(task.result())
                    saved += 1
                emit("blocked")
                raise
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await browser.close()
        emit("completed")
        logger.info(
            "scrape_finished", query=query, found=found, saved=saved, errors=errors,
            seconds=round(time.monotonic() - started, 1),
        )
        return {"found": found, "saved": saved, "errors": errors, "query": query}
    finally:
        site_pool.shutdown(wait=False, cancel_futures=True)


def browser_check_sites(urls: list[str]) -> dict[str, dict]:
    """`render_site_emails` for many sites at once (used by "Find missing emails"). Chromium resolves names itself,
    so hosts the local DNS can't resolve are mapped to their public DNS answers."""
    import asyncio

    from app.utils.dns_fallback import resolve_over_https

    if not urls:
        return {}
    rules = []
    for host in {urlparse(u).hostname for u in urls if urlparse(u).hostname}:
        addresses = resolve_over_https(host)
        if addresses:
            rules.append(f"MAP {host} {addresses[0]}")

    async def run() -> dict[str, dict]:
        from playwright.async_api import async_playwright

        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        if rules:
            args.append("--host-resolver-rules=" + ", ".join(rules))
        tabs = asyncio.Semaphore(max(1, get_settings().scraper_place_tabs))
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=args)
            try:
                context = await browser.new_context(locale="en-US", user_agent=BROWSER_UA)

                async def one(url: str) -> dict:
                    async with tabs:
                        return await render_site_emails(context, url)

                results = await asyncio.gather(*(one(u) for u in urls))
            finally:
                await browser.close()
        return dict(zip(urls, results))

    return asyncio.run(run())

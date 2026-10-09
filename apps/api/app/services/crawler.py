from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.logging_config import get_logger
from app.services.email_extraction import extract_emails_from_html
from app.utils.normalize import extract_domain, normalize_url

logger = get_logger("crawler")

RELEVANT_HINTS = (
    "contact",
    "get-in-touch",
    "getintouch",
    "enquire",
    "inquiry",
    "enquiry",
    "about",
    "service",
    "services",
    "product",
    "products",
    "pricing",
    "price",
    "book",
    "booking",
    "quote",
    "estimate",
    "consult",
    "team",
    "location",
)

PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}"
)

CTA_WORDS = (
    "book",
    "schedule",
    "get a quote",
    "request a quote",
    "contact us",
    "call now",
    "request",
    "free estimate",
    "get started",
    "send message",
    "book a call",
)
TRUST_WORDS = ("testimonial", "review", "certified", "licensed", "insured", "guarantee")
FORM_FIELD_HINTS = (
    "email",
    "e-mail",
    "name",
    "phone",
    "tel",
    "mobile",
    "message",
    "comment",
    "company",
    "first_name",
    "firstname",
    "last_name",
    "fullname",
    "full-name",
)
FORM_EMBED_HINTS = (
    "hbspt.forms",
    "hs-form",
    "hsforms.net",
    "hsforms.com",
    "typeform.com",
    "form.typeform",
    "jotform.com",
    "forms.gle",
    "docs.google.com/forms",
    "google.com/forms",
    "formstack.com",
    "wufoo.com",
    "cognitoforms",
    "microsoft.com/form",
    "office.com/form",
    "formspree.io",
    "getform.io",
    "usebasin.com",
    "convertkit",
    "ck.page",
    "klaviyo",
    "mailchimp.com",
    "list-manage.com",
    "tally.so",
    "fillout.com",
    "airtable.com/shr",
    "calendly.com",
    "wix-form",
    "wixforms",
    "corvid-form",
    "data-wix-form",
    "wpforms",
    "wpcf7",
    "wpcf7-form",
    "nf-form",
    "gform_wrapper",
    "gravityform",
    "elementor-form",
    "forminator",
    "fluentform",
    "ninja-forms",
    "contact-form-7",
    "squarespace-form",
    "sqs-form",
    "webflow-form",
    "data-wf-form",
    "shopify-contact",
    "contact-form",
    "lead-form",
    "quote-form",
    "estimate-form",
)
FORM_COPY_HINTS = (
    "send a message",
    "send us a message",
    "leave a message",
    "drop us a message",
    "get in touch",
    "request a quote",
    "request a callback",
    "request a call",
    "get a free estimate",
    "free estimate",
    "book an appointment",
    "schedule an appointment",
    "fill out the form",
    "complete the form",
    "submit your details",
    "your email",
    "your name",
    "your phone",
    "enter your email",
)


@dataclass
class CrawledPage:
    url: str
    status_code: int
    html: str
    title: str = ""
    meta_description: str = ""
    text: str = ""
    headers: dict = field(default_factory=dict)
    emails: list[dict] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    social: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class CrawlResult:
    homepage: CrawledPage | None
    pages: list[CrawledPage]
    emails: list[dict]
    phones: list[str]
    social: dict
    signals: dict
    headers: dict
    error: str = ""


def _robots_allows(base_url: str, target: str, user_agent: str) -> bool:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    try:
        parser.set_url(robots_url)
        parser.read()
        return parser.can_fetch(user_agent, target)
    except Exception:
        return True


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def detect_lead_capture(html: str, text: str = "") -> bool:
    """True if the page has a contact/lead form, embed, or clear form fields.

    Many builders (Wix, HubSpot, Typeform, Webflow) do not use a classic
    <form> with name/email/message, so we also check inputs, iframes, and widgets.
    """
    if not html:
        return False
    raw = html.lower()
    if any(hint in raw for hint in FORM_EMBED_HINTS):
        return True
    soup = BeautifulSoup(html, "lxml")
    if soup.find("input", attrs={"type": re.compile(r"^(email|tel)$", re.I)}):
        return True
    if soup.find("textarea"):
        return True
    for form in soup.find_all("form"):
        blob = " ".join(str(form)).lower()
        if any(hint in blob for hint in FORM_FIELD_HINTS):
            return True
        if form.find_all(["input", "textarea", "select"]):
            return True
    for field in soup.find_all(["input", "textarea", "select"]):
        blob = " ".join(
            [
                str(field.get("name") or ""),
                str(field.get("id") or ""),
                str(field.get("placeholder") or ""),
                str(field.get("aria-label") or ""),
                str(field.get("autocomplete") or ""),
                " ".join(field.get("class") or []),
            ]
        ).lower()
        if any(hint in blob for hint in FORM_FIELD_HINTS):
            return True
    for iframe in soup.find_all("iframe", src=True):
        src = (iframe.get("src") or "").lower()
        if any(hint in src for hint in FORM_EMBED_HINTS):
            return True
    lowered = (text or _clean_text(html)).lower()
    copy_hits = sum(1 for hint in FORM_COPY_HINTS if hint in lowered)
    if copy_hits >= 2:
        return True
    return False


def _page_signals(html: str, text: str, path: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    lower = text.lower()
    viewport = bool(soup.find("meta", attrs={"name": re.compile("viewport", re.I)}))
    nav = bool(soup.find("nav") or soup.select("header a"))
    has_form = detect_lead_capture(html, text)
    links = " ".join(a.get_text(" ", strip=True).lower() for a in soup.find_all("a"))
    has_cta = any(word in lower or word in links for word in CTA_WORDS)
    has_booking = any(word in lower for word in ("book now", "schedule", "appointment", "calendly"))
    has_contact = "contact" in lower or bool(soup.select("a[href^=mailto], a[href^=tel]"))
    path_l = path.lower()
    has_contact_page = any(part in path_l for part in ("contact", "get-in-touch", "enquire", "inquiry", "quote"))
    has_services = any(word in lower for word in ("service", "services", "what we do"))
    has_about = "about" in lower
    has_trust = any(word in lower for word in TRUST_WORDS)
    return {
        "has_viewport": viewport,
        "has_nav": nav,
        "has_structure": bool(soup.find("main") or soup.find("header") or nav),
        "has_cta": has_cta,
        "has_booking_cta": has_booking,
        "has_contact_info": has_contact,
        "has_contact_page": has_contact_page,
        "has_lead_capture": has_form,
        "has_services_info": has_services,
        "has_about": has_about,
        "has_trust_signals": has_trust,
        "word_count": len(text.split()),
        "title": bool((soup.title.string if soup.title else "") or ""),
        "meta_description": bool(soup.find("meta", attrs={"name": re.compile("description", re.I)})),
    }


def _social_links(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    social = {}
    mapping = {
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "linkedin": "linkedin.com",
        "twitter": "twitter.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for name, host in mapping.items():
            if host in href and name not in social:
                social[name] = href
    return social


def _extract_phones(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", m.group(0)).strip() for m in PHONE_RE.finditer(text or "")})


def _relevant_links(base: str, html: str, domain: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    scored: list[tuple[int, str]] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if extract_domain(href) != domain:
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean in seen:
            continue
        seen.add(clean)
        hay = f"{parsed.path} {a.get_text(' ', strip=True)}".lower()
        score = sum(3 if hint in hay else 0 for hint in RELEVANT_HINTS)
        if score:
            scored.append((score, clean))
    scored.sort(reverse=True)
    return [url for _, url in scored]


def crawl_website(
    url: str, *, max_pages: int | None = None, timeout: int | None = None, user_agent: str | None = None
) -> CrawlResult:
    settings = get_settings()
    start = normalize_url(url)
    domain = extract_domain(start)
    max_pages = max_pages or settings.crawl_max_pages
    timeout = timeout or settings.crawl_timeout_seconds
    user_agent = user_agent or settings.crawl_user_agent

    pages: list[CrawledPage] = []
    emails: list[dict] = []
    phones: list[str] = []
    social: dict = {}
    signals: dict = {}
    headers: dict = {}

    robots_blocked = settings.crawl_respect_robots and not _robots_allows(start, start, user_agent)
    if robots_blocked:
        logger.info("robots_disallow_try_homepage", url=start)

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as client:
            homepage = _fetch(client, start, domain)
            pages.append(homepage)
            if homepage.html:
                emails.extend(homepage.emails)
                phones.extend(homepage.phones)
                social.update(homepage.social)
                signals = _page_signals(homepage.html, homepage.text, urlparse(homepage.url).path)
                headers = homepage.headers
                candidates = [] if robots_blocked else _relevant_links(homepage.url, homepage.html, domain)
            else:
                candidates = []

            for candidate in candidates:
                if len(pages) >= max_pages:
                    break
                if settings.crawl_respect_robots and not _robots_allows(start, candidate, user_agent):
                    continue
                page = _fetch(client, candidate, domain)
                pages.append(page)
                emails.extend(page.emails)
                phones.extend(page.phones)
                social.update({k: v for k, v in page.social.items() if k not in social})
                extra = _page_signals(page.html, page.text, urlparse(page.url).path)
                for key, value in extra.items():
                    if isinstance(value, bool):
                        signals[key] = bool(signals.get(key)) or value
                    elif key == "word_count":
                        signals[key] = int(signals.get(key) or 0) + int(value or 0)
    except Exception as exc:
        logger.error("crawl_failed", url=start, error=str(exc))
        return CrawlResult(None, pages, emails, phones, social, signals, headers, error=str(exc))

    # Dedupe emails
    unique_emails = {}
    for item in emails:
        unique_emails.setdefault(item["email"], item)
    if unique_emails:
        primary = next((e for e in unique_emails.values() if e.get("is_primary")), None)
        if not primary:
            first = next(iter(unique_emails.values()))
            first["is_primary"] = True

    reachable = bool(pages and pages[0].status_code and pages[0].status_code < 400)
    signals.update(
        {
            "reachable": reachable,
            "https": start.startswith("https://"),
            "has_email_on_site": bool(unique_emails),
            "failed_pages": sum(1 for p in pages if p.status_code >= 400 or p.error),
        }
    )
    return CrawlResult(
        homepage=pages[0] if pages else None,
        pages=pages,
        emails=list(unique_emails.values()),
        phones=sorted(set(phones)),
        social=social,
        signals=signals,
        headers=headers,
    )


def _fetch(client: httpx.Client, url: str, domain: str) -> CrawledPage:
    try:
        response = client.get(url)
        html = response.text if "text" in response.headers.get("content-type", "") or response.text else ""
        if not html and response.content:
            html = response.text
        title = ""
        meta = ""
        text = ""
        if html:
            soup = BeautifulSoup(html, "lxml")
            title = (soup.title.string or "").strip() if soup.title else ""
            desc = soup.find("meta", attrs={"name": re.compile("description", re.I)})
            meta = (desc.get("content") if desc else "") or ""
            text = _clean_text(html)
        return CrawledPage(
            url=str(response.url),
            status_code=response.status_code,
            html=html,
            title=title,
            meta_description=meta,
            text=text[:20000],
            headers={k.lower(): v for k, v in response.headers.items()},
            emails=extract_emails_from_html(html, str(response.url), domain),
            phones=_extract_phones(text),
            social=_social_links(html) if html else {},
        )
    except Exception as exc:
        logger.warning("page_fetch_failed", url=url, error=str(exc))
        return CrawledPage(url=url, status_code=0, html="", error=str(exc))

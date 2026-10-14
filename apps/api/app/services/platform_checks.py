"""Evidence-based feature checks for a website, tuned to its platform.

Every check returns one status, with the page it looked at and what it observed:

  present            the feature was seen
  confirmed_missing  checked where it must appear in the page source, and it is absent (e.g. no meta description,
                     no sitemap.xml at the standard addresses, no Product structured data on a product page)
  improvement        the feature exists but is measurably weak (e.g. many images without alt text)
  not_detected       not visible in the page source; it may still exist through a script, app or embed the
                     audit can't see (review widgets, chat, analytics loaded by a tag manager...), so it is never
                     presented as missing
  manual_review      worth a human look (e.g. exposed WordPress usernames), not a confirmed problem

Only public pages are read (homepage, the pages the crawl visited, robots.txt / sitemap.xml, one product page for
stores). A page that can't be fetched makes its checks "not_detected" or "manual_review", never "missing".
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.logging_config import get_logger

logger = get_logger("platform_checks")

STATUSES = ("confirmed_missing", "improvement", "not_detected", "manual_review", "present")
ECOMMERCE = {"Shopify", "WooCommerce", "BigCommerce", "Magento / Adobe Commerce", "PrestaShop", "Ecwid"}
FORM_EMBEDS = ("hsforms", "typeform", "jotform", "formstack", "cognitoforms", "wufoo", "tally.so", "forms.gle", "123formbuilder")


def _check(id_, feature, category, status, severity, evidence, recommendation="", business=""):
    return {
        "id": id_,
        "feature": feature,
        "category": category,
        "status": status,
        "severity": severity if status not in ("present",) else "info",
        "evidence": evidence,
        "recommendation": recommendation if status != "present" else "",
        "business_relevance": business if status != "present" else "",
    }


def _ev(url: str, observed: str) -> list[dict]:
    return [{"url": url, "observed": observed}]


def _jsonld_types(soup: BeautifulSoup) -> list[str]:
    types: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict):
                t = item.get("@type")
                types += t if isinstance(t, list) else [t] if t else []
    if soup.find(attrs={"itemtype": re.compile("schema.org", re.I)}):
        types.append("microdata")
    return [str(t) for t in types]


def _get(client: httpx.Client, url: str) -> tuple[int, str, str]:
    try:
        r = client.get(url)
        return r.status_code, r.text if "text" in r.headers.get("content-type", "") or "json" in r.headers.get("content-type", "") or "xml" in r.headers.get("content-type", "") else "", str(r.url)
    except Exception as exc:
        logger.info("check_fetch_failed", url=url, error=str(exc)[:120])
        return 0, "", url


# ---------------------------------------------------------------- checks every site gets


def common_checks(home_url: str, home_html: str, pages: list[tuple[str, str]], secondary: list[dict]) -> list[dict]:
    checks = []
    soup = BeautifulSoup(home_html or "", "lxml")
    addons = {s["name"] for s in secondary}
    categories = {s["category"] for s in secondary}

    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    if not title:
        checks.append(_check("title", "Page title", "seo", "confirmed_missing", "high", _ev(home_url, "no <title> in the homepage source"),
                             "Add a descriptive page title with the business name, service and location.",
                             "The title is what searchers see first in Google results."))
    elif len(title) < 15 or len(title) > 65:
        checks.append(_check("title", "Page title", "seo", "improvement", "low", _ev(home_url, f'title is {len(title)} characters: "{title[:70]}"'),
                             "Keep the homepage title around 30-60 characters with the main service and location.",
                             "A clear title can help the page show up and get clicked for the right searches."))
    else:
        checks.append(_check("title", "Page title", "seo", "present", "info", _ev(home_url, f'"{title[:70]}"')))

    desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    content = (desc.get("content") or "").strip() if desc else ""
    if not content:
        checks.append(_check("meta_description", "Meta description", "seo", "confirmed_missing", "medium",
                             _ev(home_url, "no meta description in the homepage source"),
                             "Write a 120-160 character description of what the business offers and where.",
                             "Without one, Google picks random page text for the search snippet."))
    elif len(content) < 70 or len(content) > 170:
        checks.append(_check("meta_description", "Meta description", "seo", "improvement", "low",
                             _ev(home_url, f"meta description is {len(content)} characters"),
                             "Aim for roughly 120-160 characters that describe the main offer.",
                             "A complete description makes the search result more informative."))
    else:
        checks.append(_check("meta_description", "Meta description", "seo", "present", "info", _ev(home_url, f"{len(content)} characters")))

    if not soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}):
        checks.append(_check("viewport", "Mobile viewport", "mobile", "confirmed_missing", "high", _ev(home_url, "no viewport meta tag"),
                             "Add a responsive viewport meta tag and check the layout on phones.",
                             "Without it, phones show a shrunk desktop page, which is hard to use."))
    else:
        checks.append(_check("viewport", "Mobile viewport", "mobile", "present", "info", _ev(home_url, "viewport meta tag found")))

    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    if not h1s:
        checks.append(_check("h1", "Main heading (H1)", "seo", "confirmed_missing", "medium", _ev(home_url, "no <h1> in the homepage source"),
                             "Give the homepage one clear H1 saying what the business does and where.",
                             "The main heading helps visitors and search engines understand the page at a glance."))
    elif len(h1s) > 1:
        checks.append(_check("h1", "Main heading (H1)", "seo", "improvement", "low", _ev(home_url, f"{len(h1s)} <h1> headings"),
                             "Use a single H1 for the main message and H2s for sections.",
                             "One clear main heading keeps the page's topic unambiguous."))
    else:
        checks.append(_check("h1", "Main heading (H1)", "seo", "present", "info", _ev(home_url, f'"{h1s[0][:80]}"')))

    types = _jsonld_types(soup)
    business_types = [t for t in types if re.search(r"business|organization|store|restaurant|service|professional|corporation|dentist|plumber|contractor|website", t, re.I)]
    if not types:
        checks.append(_check("structured_data", "Structured data (schema.org)", "seo", "confirmed_missing", "medium",
                             _ev(home_url, "no JSON-LD or microdata on the homepage"),
                             "Add LocalBusiness / Organization structured data (name, address, phone, hours).",
                             "Structured data helps search engines show the business details accurately."))
    elif not business_types:
        checks.append(_check("structured_data", "Structured data (schema.org)", "seo", "improvement", "low",
                             _ev(home_url, f"types found: {', '.join(sorted(set(types))[:6])}; no business / organization type"),
                             "Add a LocalBusiness or Organization block alongside the existing structured data.",
                             "It ties the site to the business's name, address and phone for search engines."))
    else:
        checks.append(_check("structured_data", "Structured data (schema.org)", "seo", "present", "info",
                             _ev(home_url, f"types: {', '.join(sorted(set(types))[:6])}")))

    if not soup.find("meta", attrs={"property": re.compile("^og:(title|image)$", re.I)}):
        checks.append(_check("open_graph", "Social sharing preview (Open Graph)", "seo", "improvement", "low",
                             _ev(home_url, "no og:title / og:image tags"),
                             "Add Open Graph title and image so shared links show a proper preview.",
                             "Links shared on Facebook, WhatsApp or LinkedIn look more trustworthy with a preview."))

    imgs = soup.find_all("img")
    if len(imgs) >= 5:
        no_alt = [i for i in imgs if not (i.get("alt") or "").strip()]
        share = len(no_alt) / len(imgs)
        if share >= 0.3:
            checks.append(_check("alt_text", "Image alt text", "accessibility", "improvement", "medium" if share >= 0.6 else "low",
                                 _ev(home_url, f"{len(no_alt)} of {len(imgs)} images on the homepage have no alt text"),
                                 "Describe meaningful images with short alt text.",
                                 "Alt text helps visually impaired visitors and image search."))
        else:
            checks.append(_check("alt_text", "Image alt text", "accessibility", "present", "info",
                                 _ev(home_url, f"{len(imgs) - len(no_alt)} of {len(imgs)} images have alt text")))

    if categories & {"analytics"}:
        names = sorted(a for a in addons if any(s["name"] == a and s["category"] == "analytics" for s in secondary))
        checks.append(_check("analytics", "Analytics / conversion tracking", "analytics", "present", "info", _ev(home_url, ", ".join(names))))
    else:
        checks.append(_check("analytics", "Analytics / conversion tracking", "analytics", "not_detected", "medium",
                             _ev(home_url, "no Google Analytics, Tag Manager, Meta Pixel or similar tag in the page source"),
                             "Confirm analytics and conversion tracking (calls, forms, bookings) are set up.",
                             "Tracking shows which pages and channels bring enquiries. (It may be installed in a way "
                             "the audit can't see, so this needs confirming.)"))

    all_html = " ".join(h for _, h in pages if h).lower()
    has_form = bool(re.search(r"<form[^>]*>.*?(type=\"?email|type=\"?tel|name=\"?(your-)?email)", all_html, re.S)) or any(
        e in all_html for e in FORM_EMBEDS
    ) or bool(categories & {"forms", "booking"})
    form_url = next((u for u, h in pages if h and "<form" in h.lower()), home_url)
    if has_form:
        checks.append(_check("lead_form", "Enquiry / booking form", "conversion", "present", "info",
                             _ev(form_url, "contact, quote or booking form found")))
    else:
        checks.append(_check("lead_form", "Enquiry / booking form", "conversion", "not_detected", "medium",
                             [{"url": u, "observed": "no form with an email / phone field in the page source"} for u, _ in pages[:3]]
                             or _ev(home_url, "no form with an email / phone field in the page source"),
                             "Make sure there is an easy enquiry or booking form on the main pages.",
                             "Visitors who won't call often still send a quick message. (A form loaded by a script "
                             "wouldn't be visible to the audit.)"))

    if "href=\"tel:" in all_html or "href='tel:" in all_html:
        checks.append(_check("click_to_call", "Click-to-call phone link", "mobile", "present", "info", _ev(home_url, "tel: link found")))
    else:
        checks.append(_check("click_to_call", "Click-to-call phone link", "mobile", "not_detected", "low",
                             _ev(home_url, "no tel: link on the pages checked"),
                             "Make the phone number a tappable tel: link, especially in the header on mobile.",
                             "On phones, one tap to call removes friction for ready-to-buy visitors."))

    if home_url.startswith("http://"):
        checks.append(_check("https", "HTTPS", "security", "confirmed_missing", "high", _ev(home_url, "the site loads over http://"),
                             "Install an SSL certificate and redirect all pages to https.",
                             "Browsers mark non-HTTPS sites as 'Not secure', which puts visitors off."))
    return checks


def crawl_files_checks(client: httpx.Client, home_url: str) -> list[dict]:
    base = f"{urlparse(home_url).scheme}://{urlparse(home_url).netloc}"
    checks = []
    status, robots, robots_url = _get(client, f"{base}/robots.txt")
    sitemap_in_robots = re.findall(r"(?im)^sitemap:\s*(\S+)", robots or "")
    if status == 0:
        checks.append(_check("robots", "robots.txt", "seo", "not_detected", "low", _ev(robots_url, "couldn't be fetched")))
    elif status >= 400:
        checks.append(_check("robots", "robots.txt", "seo", "confirmed_missing", "low", _ev(robots_url, f"HTTP {status}"),
                             "Add a robots.txt that points search engines to the sitemap.",
                             "It guides search engines through the site."))
    else:
        checks.append(_check("robots", "robots.txt", "seo", "present", "info", _ev(robots_url, "found")))
    found = None
    tried = []
    for url in [*sitemap_in_robots[:2], f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/wp-sitemap.xml"]:
        if url in tried:
            continue
        tried.append(url)
        s, body, final = _get(client, url)
        if s and s < 400 and ("<urlset" in body or "<sitemapindex" in body):
            found = final
            break
    if found:
        checks.append(_check("sitemap", "XML sitemap", "seo", "present", "info", _ev(found, "sitemap found")))
    elif tried:
        checks.append(_check("sitemap", "XML sitemap", "seo", "confirmed_missing", "medium",
                             [{"url": u, "observed": "no sitemap at this address"} for u in tried[:3]],
                             "Publish an XML sitemap and submit it in Google Search Console.",
                             "A sitemap helps search engines find every important page."))
    return checks


# ---------------------------------------------------------------- platform specific


def _product_url(client: httpx.Client, platform: str, home_url: str, pages: list[tuple[str, str]]) -> str | None:
    base = f"{urlparse(home_url).scheme}://{urlparse(home_url).netloc}"
    if platform == "Shopify":
        status, body, _ = _get(client, f"{base}/products.json?limit=1")
        try:
            products = json.loads(body).get("products") or [] if status == 200 else []
        except ValueError:
            products = []
        if products and products[0].get("handle"):
            return f"{base}/products/{products[0]['handle']}"
    patterns = {
        "Shopify": r"/products/[^\"'#?\s]+",
        "WooCommerce": r"/product/[^\"'#?\s]+",
        "BigCommerce": r"/products?/[^\"'#?\s]+",
        "Magento / Adobe Commerce": r"/[a-z0-9-]+\.html",
        "PrestaShop": r"/[0-9]+-[a-z0-9-]+\.html",
    }
    pattern = patterns.get(platform, r"/products?/[^\"'#?\s]+")
    for url, html in pages:
        for href in re.findall(r'href="([^"]+)"', html or ""):
            full = urljoin(url, href)
            if urlparse(full).netloc == urlparse(home_url).netloc and re.search(pattern, urlparse(full).path):
                return full.split("#")[0]
    return None


def ecommerce_checks(client: httpx.Client, platform: str, home_url: str, home_html: str, pages, secondary) -> list[dict]:
    checks = []
    addons = {s["name"]: s["category"] for s in secondary}
    product_url = _product_url(client, platform, home_url, pages)
    if not product_url:
        checks.append(_check("product_page", "Product page", "ecommerce", "manual_review", "low", _ev(home_url, "no product page found to inspect"),
                             "Review a product page by hand (photos, details, reviews, add-to-cart on mobile).",
                             "Product pages are where most buying decisions happen."))
        return checks
    status, html, final = _get(client, product_url)
    if not html:
        checks.append(_check("product_page", "Product page", "ecommerce", "manual_review", "low", _ev(product_url, f"couldn't be loaded (HTTP {status})")))
        return checks
    soup = BeautifulSoup(html, "lxml")
    low = html.lower()
    types = _jsonld_types(soup)
    if any("product" in t.lower() for t in types):
        checks.append(_check("product_schema", "Product structured data", "seo", "present", "info", _ev(final, "Product JSON-LD found")))
    else:
        checks.append(_check("product_schema", "Product structured data", "seo", "confirmed_missing", "medium",
                             _ev(final, "no Product structured data on this product page"),
                             "Add Product structured data (price, availability, reviews) to product pages.",
                             "It lets Google show price and stock details in search results."))
    review_apps = [n for n, c in addons.items() if c == "reviews"]
    if review_apps or "aggregaterating" in low or re.search(r"(customer )?reviews?\b.*\b(stars?|rating)", low):
        checks.append(_check("reviews", "Product reviews", "trust", "present", "info",
                             _ev(final, ", ".join(review_apps) or "review / rating markup found")))
    else:
        checks.append(_check("reviews", "Product reviews", "trust", "not_detected", "medium",
                             _ev(final, "no review app or rating markup in this product page's source"),
                             "Check whether reviews show on product pages; if not, add a review app and ask past buyers.",
                             "Credible reviews help shoppers decide. (A review widget loaded by a script wouldn't be "
                             "visible to the audit, so confirm on the live page.)"))
    if "product-recommendations" in low or "related products" in low or "you may also like" in low or any(
        c == "recommendations" for c in addons.values()
    ):
        checks.append(_check("recommendations", "Related products / cross-sell", "ecommerce", "present", "info", _ev(final, "recommendation section found")))
    else:
        checks.append(_check("recommendations", "Related products / cross-sell", "ecommerce", "not_detected", "low",
                             _ev(final, "no related-products section in the page source"),
                             "Show complementary products on product and cart pages.",
                             "Relevant suggestions help shoppers discover more items and can raise order value."))
    home_low = (home_html or "").lower()
    if 'action="/search' in home_low or 'type="search"' in home_low or "predictive-search" in home_low:
        checks.append(_check("search", "Store search", "ecommerce", "present", "info", _ev(home_url, "search form found")))
    else:
        checks.append(_check("search", "Store search", "ecommerce", "not_detected", "low", _ev(home_url, "no search form in the homepage source"),
                             "Make product search easy to find, ideally with suggestions as you type.",
                             "Shoppers who search usually know what they want."))
    email_tools = [n for n, c in addons.items() if c == "email"]
    newsletter = re.search(r"(newsletter|subscribe|sign up)[^<]{0,80}</", home_low) and 'type="email"' in home_low
    if email_tools or newsletter:
        checks.append(_check("email_capture", "Email capture / abandoned-cart tools", "retention", "present", "info",
                             _ev(home_url, ", ".join(email_tools) or "newsletter sign-up form found")))
    else:
        checks.append(_check("email_capture", "Email capture / abandoned-cart tools", "retention", "not_detected", "medium",
                             _ev(home_url, "no email tool (Klaviyo, Omnisend, Mailchimp...) or sign-up form in the source"),
                             "Capture emails (welcome offer) and set up abandoned-cart reminders.",
                             "Bringing back visitors who left items in the cart is one of the cheapest ways to win sales."))
    return checks


def wordpress_checks(client: httpx.Client, home_url: str, home_html: str, secondary) -> list[dict]:
    checks = []
    base = f"{urlparse(home_url).scheme}://{urlparse(home_url).netloc}"
    addons = {s["name"]: s["category"] for s in secondary}
    seo = [n for n, c in addons.items() if c == "seo"]
    if seo:
        checks.append(_check("seo_plugin", "SEO plugin", "seo", "present", "info", _ev(home_url, ", ".join(seo))))
    else:
        checks.append(_check("seo_plugin", "SEO plugin", "seo", "not_detected", "low",
                             _ev(home_url, "no Yoast / Rank Math / AIOSEO markers in the page source"),
                             "Confirm an SEO plugin manages titles, descriptions and the sitemap.",
                             "It keeps metadata consistent across every page. (Some setups hide plugin markers.)"))
    version = re.search(r'generator" content="wordpress ([0-9.]+)', (home_html or "").lower())
    if version:
        checks.append(_check("wp_version", "WordPress version exposed", "security", "improvement", "low",
                             _ev(home_url, f"generator tag shows WordPress {version.group(1)}"),
                             "Hide the version number and keep WordPress, themes and plugins updated.",
                             "A visible version makes it easier for bots to target known weaknesses."))
    status, body, final = _get(client, f"{base}/wp-json/wp/v2/users?per_page=1")
    if status == 200 and '"slug"' in body:
        checks.append(_check("wp_users", "Usernames visible through the REST API", "security", "manual_review", "low",
                             _ev(final, "the public users endpoint lists account names"),
                             "Have the developer restrict the users endpoint.",
                             "Exposed usernames make password-guessing attacks easier."))
    perf = [n for n, c in addons.items() if c == "performance"]
    if perf:
        checks.append(_check("caching", "Caching / speed plugin", "performance", "present", "info", _ev(home_url, ", ".join(perf))))
    else:
        checks.append(_check("caching", "Caching / speed plugin", "performance", "not_detected", "low",
                             _ev(home_url, "no WP Rocket / LiteSpeed markers in the page source"),
                             "Check caching and image optimisation (see the speed results).",
                             "Faster pages keep more visitors. (Server-level caching isn't visible to the audit.)"))
    builders = [n for n, c in addons.items() if c == "page_builder"]
    if builders:
        checks.append(_check("page_builder", "Page builder", "performance", "present", "info", _ev(home_url, ", ".join(builders))))
    return checks


def builder_checks(platform: str, home_url: str, pages, secondary) -> list[dict]:
    checks = []
    addons = {s["name"]: s["category"] for s in secondary}
    all_low = " ".join(h for _, h in pages if h).lower()
    booking = [n for n, c in addons.items() if c == "booking"]
    if booking or re.search(r"(book now|book online|book an appointment|schedule (a|your))", all_low):
        checks.append(_check("booking", "Online booking", "conversion", "present", "info", _ev(home_url, ", ".join(booking) or "booking call-to-action found")))
    else:
        checks.append(_check("booking", "Online booking", "conversion", "not_detected", "low",
                             _ev(home_url, "no booking tool or 'book now' action on the pages checked"),
                             f"If the business takes appointments, {platform} supports built-in booking or scheduling integrations.",
                             "Letting visitors book without calling captures out-of-hours enquiries."))
    if platform == "Webflow":
        if "w-dyn-list" in all_low:
            checks.append(_check("webflow_cms", "Webflow CMS content", "content", "present", "info", _ev(home_url, "CMS collection lists found")))
        if "w-form" in all_low:
            checks.append(_check("webflow_forms", "Webflow forms", "conversion", "present", "info", _ev(home_url, "native form found")))
    return checks


BLOCK_MARKERS = (
    "cf-chl", "just a moment...", "attention required", "access denied", "captcha", "are you a robot",
    "enable javascript and cookies", "ddos protection", "request unsuccessful", "incapsula", "sucuri website firewall",
)


def page_unreadable(html: str, status: int | None = None) -> str:
    """Why this page can't be judged ("" = it can): an error, a bot challenge, or an almost empty page."""
    low = (html or "").lower()
    if status and status >= 400:
        return f"the site answered HTTP {status}"
    if not low.strip():
        return "the page came back empty"
    if any(m in low for m in BLOCK_MARKERS) and len(low) < 60000:
        return "the site showed a bot-protection / challenge page instead of its content"
    if len(low) < 1500 and "<body" in low and low.count("<") < 40:
        return "the page was almost empty (content may be loaded only by scripts)"
    return ""


def run_platform_checks(home_url: str, pages: list[tuple[str, str]], detection, home_status: int | None = None) -> dict:
    """All checks for one website. Never raises: a failure is recorded and the audit goes on."""
    platform = detection.platform_name
    secondary = detection.secondary
    home_html = pages[0][1] if pages else ""
    checks: list[dict] = []
    blocked = page_unreadable(home_html, home_status)
    if blocked:
        # Judging a block / error page would invent problems: nothing is called missing, a human should look
        checks.append(_check("unreadable", "Website could be read", "other", "manual_review", "low",
                             _ev(home_url, blocked),
                             "Check the site in a browser; automated checks couldn't read it.",
                             "The audit can only report what it can actually see."))
        return {"checks": checks, "counts": {s: sum(1 for c in checks if c["status"] == s) for s in STATUSES}, "unreadable": blocked}
    try:
        checks += common_checks(home_url, home_html, pages, secondary)
        with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            checks += crawl_files_checks(client, home_url)
            platforms = {platform} | {s["name"] for s in secondary if s["category"] == "platform"}
            if platforms & ECOMMERCE:
                store = next(p for p in [platform, *sorted(platforms)] if p in ECOMMERCE)
                checks += ecommerce_checks(client, store, home_url, home_html, pages, secondary)
            if platforms & {"WordPress", "WooCommerce"}:
                checks += wordpress_checks(client, home_url, home_html, secondary)
            if platform in {"Wix", "Squarespace", "Webflow", "Showit", "GoDaddy Website Builder", "Duda", "Weebly / Square Online"}:
                checks += builder_checks(platform, home_url, pages, secondary)
    except Exception as exc:  # checks are a bonus: never fail the audit
        logger.warning("platform_checks_failed", url=home_url, error=str(exc)[:200])
        checks.append(_check("checks_error", "Feature checks", "other", "manual_review", "low", _ev(home_url, f"checks stopped early: {str(exc)[:120]}")))
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in STATUSES}
    return {"checks": checks, "counts": counts}

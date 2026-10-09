"""Collect the raw facts Claude needs to analyse a website's performance itself.

No third-party testing service is used. We fetch the live homepage and every file it loads,
record what we measured (timings, sizes, compression, caching), and keep an excerpt of the
HTML source so Claude can read the page the way a developer would.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.logging_config import get_logger
from app.utils.normalize import extract_domain, normalize_url

logger = get_logger("performance")

MAX_PAGE_BYTES = 5_000_000
MAX_RESOURCES = 60
MAX_RESOURCE_BYTES = 10_000_000
RESOURCE_TIMEOUT = 10
HEAD_EXCERPT_CHARS = 12_000
BODY_EXCERPT_CHARS = 20_000
# Only encodings httpx can decode without extra packages.
ACCEPT_ENCODING = "gzip, deflate"
TEXT_TYPES = {"script", "stylesheet"}
MODERN_IMAGE_EXTENSIONS = (".webp", ".avif", ".svg")


# Measured as a normal visitor's browser: many business sites' firewalls refuse unknown bots outright.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class PerformanceError(RuntimeError):
    pass


class PerformanceBlockedError(PerformanceError):
    """The site is up but blocks automated speed tests; the audit continues on screenshots and HTML instead."""


FIREWALL_STATUSES = {401, 403, 429, 503}


def _headers() -> dict:
    return {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": ACCEPT_ENCODING,
    }


def analyze_html(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html or "", "lxml")
    head = soup.head or soup
    images = soup.find_all("img")
    scripts = soup.find_all("script", src=True)
    blocking_scripts = [s for s in head.find_all("script", src=True) if not (s.has_attr("async") or s.has_attr("defer"))]
    stylesheets = soup.find_all("link", rel=lambda value: value and "stylesheet" in value)
    is_https = page_url.startswith("https://")
    mixed = 0
    if is_https:
        for tag in soup.find_all(["img", "script", "iframe", "link"]):
            src = tag.get("src") or (tag.get("href") if tag.name == "link" else "")
            if isinstance(src, str) and src.startswith("http://"):
                mixed += 1
    title = soup.title.get_text(strip=True) if soup.title else ""
    description = soup.find("meta", attrs={"name": "description"})
    html_tag = soup.find("html")
    return {
        "title": title,
        "title_length": len(title),
        "meta_description_length": len((description.get("content") or "").strip()) if description else 0,
        "has_viewport": bool(soup.find("meta", attrs={"name": "viewport"})),
        "has_lang": bool(html_tag and html_tag.get("lang")),
        "has_canonical": bool(soup.find("link", rel=lambda value: value and "canonical" in value)),
        "has_open_graph": bool(soup.find("meta", attrs={"property": "og:title"})),
        "structured_data_blocks": len(soup.find_all("script", type="application/ld+json")),
        "h1_count": len(soup.find_all("h1")),
        "image_count": len(images),
        "images_missing_alt": sum(1 for img in images if not (img.get("alt") or "").strip()),
        "images_without_lazy_loading": sum(1 for img in images if img.get("loading") != "lazy"),
        "images_without_dimensions": sum(1 for img in images if not (img.get("width") and img.get("height"))),
        "external_scripts": len(scripts),
        "inline_scripts": len(soup.find_all("script", src=False)),
        "render_blocking_scripts": len(blocking_scripts),
        "stylesheets": len(stylesheets),
        "iframes": len(soup.find_all("iframe")),
        "mixed_content_resources": mixed,
    }


def html_excerpt(html: str) -> str:
    """The page source for Claude: full <head>, body with inline script/style bodies elided."""
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup.find_all(["script", "style"]):
        if tag.string and len(tag.string) > 300:
            tag.string = f"/* inline {tag.name}: {len(tag.string)} chars elided */"
    for svg in soup.find_all("svg"):
        svg.clear()
    head = str(soup.head) if soup.head else ""
    body = str(soup.body) if soup.body else str(soup)
    body = re.sub(r"\n\s*\n+", "\n", body)
    excerpt = head[:HEAD_EXCERPT_CHARS] + "\n" + body[:BODY_EXCERPT_CHARS]
    if len(body) > BODY_EXCERPT_CHARS:
        excerpt += f"\n<!-- {len(body) - BODY_EXCERPT_CHARS} more characters of body not shown -->"
    return excerpt


def resource_urls(html: str, page_url: str) -> list[tuple[str, str]]:
    """(type, absolute url) for every file the homepage loads, scripts and styles first."""
    soup = BeautifulSoup(html or "", "lxml")
    found: list[tuple[str, str]] = []
    for tag in soup.find_all("script", src=True):
        found.append(("script", tag["src"]))
    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel") or []).lower()
        if "stylesheet" in rel:
            found.append(("stylesheet", tag["href"]))
        elif "preload" in rel and tag.get("as") == "font":
            found.append(("font", tag["href"]))
        elif "icon" in rel:
            found.append(("image", tag["href"]))
    for tag in soup.find_all(["img", "source"]):
        src = tag.get("src") or (tag.get("srcset") or "").split(",")[0].strip().split(" ")[0]
        if src:
            found.append(("image", src))
    for tag in soup.find_all(["video", "audio"], src=True):
        found.append(("media", tag["src"]))

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for kind, raw in found:
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            continue
        url = urljoin(page_url, raw.strip())
        if urlparse(url).scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        out.append((kind, url))
    return out[:MAX_RESOURCES]


def _fetch_resource(client: httpx.Client, kind: str, url: str, site_domain: str) -> dict:
    item = {
        "url": url,
        "type": kind,
        "third_party": extract_domain(url) not in {site_domain, ""},
        "status": None,
        "bytes": None,
        "content_type": "",
        "compression": "",
        "cache_control": "",
        "duration_ms": None,
        "error": "",
    }
    start = time.perf_counter()
    try:
        # A GET of the raw bytes gives the real transfer size even when servers omit Content-Length.
        with client.stream("GET", url) as response:
            size = 0
            for chunk in response.iter_raw():
                size += len(chunk)
                if size > MAX_RESOURCE_BYTES:
                    break
            headers = {k.lower(): v for k, v in response.headers.items()}
            item.update(
                status=response.status_code,
                bytes=size,
                content_type=headers.get("content-type", "").split(";")[0],
                compression=headers.get("content-encoding", ""),
                cache_control=headers.get("cache-control", ""),
            )
    except httpx.HTTPError as exc:
        item["error"] = exc.__class__.__name__
    item["duration_ms"] = round((time.perf_counter() - start) * 1000)
    return item


def _cacheable(cache_control: str) -> bool:
    value = (cache_control or "").lower()
    if not value or "no-store" in value or "no-cache" in value:
        return False
    match = re.search(r"max-age=(\d+)", value)
    return not match or int(match.group(1)) >= 3600


def summarize_resources(items: list[dict], html_transfer_bytes: int) -> dict:
    by_type: dict[str, dict] = {}
    for item in items:
        bucket = by_type.setdefault(item["type"], {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += item["bytes"] or 0
    ok = [i for i in items if i["status"] and i["status"] < 400]
    third_party = [i for i in items if i["third_party"]]
    largest = sorted(ok, key=lambda i: i["bytes"] or 0, reverse=True)[:8]
    return {
        "total_requests": len(items) + 1,
        "total_bytes": sum(i["bytes"] or 0 for i in items) + html_transfer_bytes,
        "html_bytes": html_transfer_bytes,
        "by_type": by_type,
        "third_party_requests": len(third_party),
        "third_party_domains": sorted({extract_domain(i["url"]) for i in third_party})[:15],
        "failed": [{"url": i["url"], "status": i["status"], "error": i["error"]} for i in items if i["error"] or (i["status"] or 0) >= 400][:10],
        "uncompressed_text_files": [
            i["url"] for i in ok if i["type"] in TEXT_TYPES and not i["compression"] and (i["bytes"] or 0) > 2048
        ][:10],
        "short_or_no_cache": [i["url"] for i in ok if not _cacheable(i["cache_control"])][:10],
        "large_images": [
            {"url": i["url"], "bytes": i["bytes"]} for i in ok if i["type"] == "image" and (i["bytes"] or 0) > 200_000
        ][:10],
        "legacy_format_images": [
            i["url"]
            for i in ok
            if i["type"] == "image"
            and (i["bytes"] or 0) > 50_000
            and not urlparse(i["url"]).path.lower().endswith(MODERN_IMAGE_EXTENSIONS)
            and i["content_type"] not in {"image/webp", "image/avif", "image/svg+xml"}
        ][:10],
        "largest": [{"url": i["url"], "type": i["type"], "bytes": i["bytes"]} for i in largest],
        "slowest": [
            {"url": i["url"], "duration_ms": i["duration_ms"]}
            for i in sorted(ok, key=lambda i: i["duration_ms"] or 0, reverse=True)[:5]
        ],
    }


def probe_website(url: str) -> tuple[dict, str]:
    """Fetch the homepage once, timing it. Returns (measurements, html)."""
    settings = get_settings()
    start = time.perf_counter()
    try:
        with httpx.Client(follow_redirects=True, timeout=settings.crawl_timeout_seconds, headers=_headers()) as client:
            with client.stream("GET", url) as response:
                ttfb_ms = round((time.perf_counter() - start) * 1000)
                html_bytes = b""
                for chunk in response.iter_bytes():
                    html_bytes += chunk
                    if len(html_bytes) > MAX_PAGE_BYTES:
                        break
                total_ms = round((time.perf_counter() - start) * 1000)
                # Bytes on the wire (compressed), as opposed to the decoded HTML size.
                transfer_bytes = response.num_bytes_downloaded
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                final_url = str(response.url)
                status_code = response.status_code
                redirects = len(response.history)
                html = html_bytes.decode(response.encoding or "utf-8", errors="replace")
    except httpx.HTTPError as exc:
        raise PerformanceError(f"Website could not be reached: {exc.__class__.__name__}: {str(exc)[:160]}") from exc
    if status_code in FIREWALL_STATUSES:
        raise PerformanceBlockedError(f"The website's firewall refused the speed test (HTTP {status_code}).")
    if status_code >= 400:
        raise PerformanceError(f"Website returned HTTP {status_code} for {final_url}")

    parsed = urlparse(final_url)
    return (
        {
            "status_code": status_code,
            "final_url": final_url,
            "redirects": redirects,
            "https": parsed.scheme == "https",
            "ttfb_ms": ttfb_ms,
            "load_ms": total_ms,
            "transfer_bytes": transfer_bytes,
            "html_bytes": len(html_bytes),
            "compression": response_headers.get("content-encoding", ""),
            "cache_control": response_headers.get("cache-control", ""),
            "hsts": "strict-transport-security" in response_headers,
            "server": response_headers.get("server", ""),
            "powered_by": response_headers.get("x-powered-by", ""),
            "page": analyze_html(html, final_url),
        },
        html,
    )


def measure_performance(url: str) -> dict:
    """Measure the live homepage and every file it loads. Raises if the site can't be reached."""
    target = normalize_url(url)
    probe, html = probe_website(target)
    final_url = probe["final_url"]
    site_domain = extract_domain(final_url)
    urls = resource_urls(html, final_url)
    with httpx.Client(follow_redirects=True, timeout=RESOURCE_TIMEOUT, headers=_headers()) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(lambda pair: _fetch_resource(client, pair[0], pair[1], site_domain), urls))
    result = {
        "url": target,
        "measured_at": datetime.now(UTC).isoformat(),
        "probe": probe,
        "resources": summarize_resources(items, probe["transfer_bytes"]),
        "resources_checked": len(items),
        "html_excerpt": html_excerpt(html),
    }
    logger.info(
        "performance_measured",
        url=target,
        ttfb_ms=probe["ttfb_ms"],
        requests=result["resources"]["total_requests"],
        total_bytes=result["resources"]["total_bytes"],
    )
    return result

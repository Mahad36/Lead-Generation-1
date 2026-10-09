from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.logging_config import get_logger

logger = get_logger("platform")


@dataclass
class DetectionResult:
    platform_name: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    technologies: list[dict] = field(default_factory=list)


FINGERPRINTS: list[tuple[str, list[tuple[str, int]]]] = [
    (
        "shopify",
        [
            ("cdn.shopify.com", 40),
            ("shopify.theme", 25),
            ("Shopify.theme", 25),
            ("myshopify.com", 30),
            ("x-shopify", 35),
            ("shopify-section", 20),
            ("window.Shopify", 25),
        ],
    ),
    (
        "woocommerce",
        [
            ("woocommerce", 35),
            ("wp-content/plugins/woocommerce", 40),
            ("wc-ajax", 20),
        ],
    ),
    (
        "wordpress",
        [
            ("wp-content", 30),
            ("wp-includes", 25),
            ("wordpress", 10),
            ("/wp-json/", 20),
            ("generator\" content=\"wordpress", 30),
        ],
    ),
    (
        "wix",
        [
            ("wix.com", 20),
            ("static.wixstatic.com", 40),
            ("X-Wix-", 35),
            ("wix-thunderbolt", 25),
            ("_wixCIDX", 20),
        ],
    ),
    (
        "webflow",
        [
            ("webflow", 30),
            ("assets.website-files.com", 35),
            ("wf-page", 20),
            ("data-wf-site", 40),
        ],
    ),
    (
        "squarespace",
        [
            ("squarespace", 35),
            ("static1.squarespace.com", 40),
            ("sqsp-cdn", 20),
            ("squarespace-cdn", 25),
        ],
    ),
    (
        "showit",
        [
            ("showit", 40),
            ("cdn.showit.co", 40),
            ("showitapp", 20),
        ],
    ),
    (
        "bigcommerce",
        [
            ("bigcommerce", 35),
            ("cdn11.bigcommerce.com", 40),
            ("stencil-utils", 20),
        ],
    ),
    (
        "magento",
        [
            ("mage/cookies", 25),
            ("Magento_", 35),
            ("static/version", 10),
            ("mage-cache", 20),
        ],
    ),
    (
        "drupal",
        [
            ("drupal.js", 30),
            ("Drupal.settings", 35),
            ("sites/default/files", 20),
            ("generator\" content=\"drupal", 35),
        ],
    ),
    (
        "ghost",
        [
            ("ghost", 15),
            ("content/themes/", 15),
            ("generator\" content=\"ghost", 40),
            ("ghost-sdk", 25),
        ],
    ),
]


def detect_platform(
    html: str = "",
    headers: dict | None = None,
    cookies: dict | None = None,
    url: str = "",
) -> DetectionResult:
    blob_parts = [html or ""]
    header_blob = ""
    if headers:
        header_blob = " ".join(f"{k}:{v}" for k, v in headers.items())
        blob_parts.append(header_blob)
    if cookies:
        blob_parts.append(" ".join(f"{k}={v}" for k, v in cookies.items()))
    if url:
        blob_parts.append(url)
    blob = "\n".join(blob_parts)
    blob_l = blob.lower()
    header_l = header_blob.lower()

    scores: dict[str, int] = {}
    found: dict[str, list[str]] = {}
    for platform, rules in FINGERPRINTS:
        for needle, weight in rules:
            if needle.lower() in blob_l:
                scores[platform] = scores.get(platform, 0) + weight
                found.setdefault(platform, []).append(needle)

    # WooCommerce implies WordPress
    if scores.get("woocommerce", 0) >= 35:
        scores["wordpress"] = max(scores.get("wordpress", 0), 40)
        found.setdefault("wordpress", []).append("woocommerce-implies-wordpress")

    if not scores:
        custom_signals = []
        path = urlparse(url).path if url else ""
        if html and ("<html" in blob_l or "<body" in blob_l):
            custom_signals.append("html-document-present")
        if path and path not in {"", "/"}:
            custom_signals.append("custom-path")
        if custom_signals:
            return DetectionResult("Other", 0.45, custom_signals, [{"name": "Other", "confidence": 0.45, "signals": custom_signals, "is_primary_platform": True}])
        return DetectionResult("Unknown", 0.0, [], [])

    labels = {
        "shopify": "Shopify",
        "woocommerce": "WooCommerce",
        "wordpress": "WordPress",
        "wix": "Wix",
        "webflow": "Webflow",
        "squarespace": "Squarespace",
        "showit": "Showit",
        "bigcommerce": "BigCommerce",
        "magento": "Magento",
        "drupal": "Drupal",
        "ghost": "Ghost",
    }
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    raw_platform, raw = ranked[0]
    platform = labels.get(raw_platform, raw_platform.title() if raw_platform else "Unknown")
    confidence = min(0.99, raw / 100)
    # Shopify header is strong
    if raw_platform == "shopify" and "x-shopify" in header_l:
        confidence = max(confidence, 0.92)

    technologies = []
    for name, value in ranked:
        label = labels.get(name, name.title() if name else "Unknown")
        technologies.append(
            {
                "name": label,
                "confidence": min(0.99, value / 100),
                "signals": found.get(name, []),
                "is_primary_platform": name == raw_platform,
            }
        )
    logger.info("platform_detected", platform=platform, confidence=confidence, url=url)
    return DetectionResult(platform, confidence, found.get(platform, []), technologies)

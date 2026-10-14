"""Which platform a website runs on, and which other technologies it uses, from public signals only.

Signals: the HTML (generator meta, asset paths, platform scripts and markup), response headers and cookies. Each
platform scores points per matching signal; the best score is the primary platform when it is convincing. A store
can be several things at once (WooCommerce on WordPress), so the other matches and add-on technologies (review apps,
email tools, analytics, page builders...) are returned separately as secondary technologies.

Nothing is guessed. A readable page with no CMS / builder signature is "Custom-built", with the technology it shows
(a web framework such as Next.js, or a server stack such as ASP.NET or PHP) and who built it when the page credits a
web agency. A page that names its generator (<meta name="generator">) gets that name. "Unknown" is left for pages
that couldn't be read (blocked, empty). Confidence: High >= 0.8, Medium >= 0.5, Low > 0, Unknown = 0.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

from app.logging_config import get_logger

logger = get_logger("platform")


@dataclass
class DetectionResult:
    platform_name: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    # every platform that matched, primary first: {name, confidence, signals, is_primary_platform}
    technologies: list[dict] = field(default_factory=list)
    confidence_label: str = "Unknown"
    # add-on technologies: {name, category, signals}
    secondary: list[dict] = field(default_factory=list)
    # what was observed: {signal, source}
    evidence: list[dict] = field(default_factory=list)


# (key, label, [(needle, weight)]); needles are matched case-insensitively against html + headers + cookies + url
FINGERPRINTS: list[tuple[str, str, list[tuple[str, int]]]] = [
    ("shopify", "Shopify", [
        ("cdn.shopify.com", 40), ("shopify.theme", 25), ("myshopify.com", 30), ("x-shopify", 35),
        ("shopify-section", 20), ("window.shopify", 25), ("shopify-digital-wallet", 20),
    ]),
    ("woocommerce", "WooCommerce", [
        ("wp-content/plugins/woocommerce", 40), ("woocommerce", 35), ("wc-ajax", 20), ("wc-block", 15),
    ]),
    ("wordpress", "WordPress", [
        ("wp-content", 30), ("wp-includes", 25), ("/wp-json/", 20), ('generator" content="wordpress', 30),
        ("wordpress", 10),
    ]),
    ("wix", "Wix", [
        ("static.wixstatic.com", 40), ("x-wix-", 35), ("wix-thunderbolt", 25), ("_wixcidx", 20), ("wix.com", 20),
    ]),
    ("squarespace", "Squarespace", [
        ("static1.squarespace.com", 40), ("squarespace-cdn", 25), ("sqsp-cdn", 20), ("squarespace", 35),
    ]),
    ("webflow", "Webflow", [
        ("data-wf-site", 40), ("assets.website-files.com", 35), ("website-files.com", 25), ("wf-page", 20),
        ("webflow", 30),
    ]),
    ("magento", "Magento / Adobe Commerce", [
        ("magento_", 35), ("mage/cookies", 25), ("mage-cache", 20), ("x-magento", 35), ("requirejs-config", 10),
        ("static/version", 10),
    ]),
    ("bigcommerce", "BigCommerce", [
        ("cdn11.bigcommerce.com", 40), ("bigcommerce", 35), ("stencil-utils", 20),
    ]),
    ("prestashop", "PrestaShop", [
        ('generator" content="prestashop', 40), ("prestashop", 35), ("/modules/ps_", 25), ("var prestashop", 30),
    ]),
    ("showit", "Showit", [("cdn.showit.co", 40), ("showit", 40), ("showitapp", 20)]),
    ("drupal", "Drupal", [
        ("drupal.js", 30), ("drupal.settings", 35), ("sites/default/files", 20), ('generator" content="drupal', 35),
        ("x-drupal", 35),
    ]),
    ("joomla", "Joomla", [('generator" content="joomla', 40), ("/media/jui/", 25), ("joomla", 20)]),
    ("ghost", "Ghost", [('generator" content="ghost', 40), ("ghost-sdk", 25), ("content/themes/", 15)]),
    ("hubspot", "HubSpot CMS", [("hs-sites.com", 35), ("hubspotusercontent", 30), ('generator" content="hubspot', 40)]),
    ("godaddy", "GoDaddy Website Builder", [("img1.wsimg.com", 35), ("godaddy website builder", 40), ("wsimg.com", 20)]),
    ("weebly", "Weebly / Square Online", [("weebly.com", 35), ("editmysite.com", 35), ("square online", 25)]),
    ("duda", "Duda", [("dudamobile", 35), ("multiscreensite", 35), ("irp.cdn-website.com", 35)]),
    ("framer", "Framer", [("framerusercontent.com", 40), ("framer.com", 25), ('generator" content="framer', 40)]),
    ("jimdo", "Jimdo", [("jimdo", 35), ("jimcdn.com", 35)]),
    ("ecwid", "Ecwid", [("app.ecwid.com", 40), ("ecwid", 30)]),
    # More site builders (asset hosts / generator tags, not words that could appear in page text)
    ("strikingly", "Strikingly", [("strikinglycdn.com", 40), ('generator" content="strikingly', 40)]),
    ("carrd", "Carrd", [("carrd.co", 35), ('generator" content="carrd', 40)]),
    ("tilda", "Tilda", [("tildacdn.com", 40), ('generator" content="tilda', 40)]),
    ("webnode", "Webnode", [("webnode.page", 35), ("webnode.com", 30), ('generator" content="webnode', 40)]),
    ("google_sites", "Google Sites", [("sites.google.com", 30), ("gstatic.com/atari", 40)]),
    ("hostinger", "Hostinger Website Builder", [("zyrosite.com", 40), ("userapp.zyrosite", 40), ("zyro", 20)]),
    ("site123", "SITE123", [("site123.me", 35), ("cdn-cms.f-static", 30), ("site123", 20)]),
    ("leadpages", "Leadpages", [("lpages.co", 40), ("leadpages", 30)]),
    ("unbounce", "Unbounce", [("unbounce.com", 35), ("ubembed", 30)]),
    ("clickfunnels", "ClickFunnels", [("clickfunnels.com", 40), ("cfimg.com", 30)]),
    ("kajabi", "Kajabi", [("kajabi-cdn.com", 40), ("kajabi-storefronts", 35)]),
    ("thinkific", "Thinkific", [("thinkific.com", 35), ("thinkific-", 25)]),
    ("bubble", "Bubble", [("cdn.bubble.io", 40), ('generator" content="bubble', 40)]),
    ("mobirise", "Mobirise", [('generator" content="mobirise', 40), ("mobirise", 25)]),
    ("yola", "Yola", [("yola.com", 30), ("yolacdn", 35)]),
    ("hibu", "Hibu", [("hibu.com", 30), ("hibusites", 35)]),
    ("thryv", "Thryv", [("thryv.com", 30), ("thryvsites", 35)]),
    ("scorpion", "Scorpion", [("scorpion.co", 35), ("scorpioncms", 40)]),
    # Website platforms for dental / medical practices
    ("prosites", "ProSites", [("prosites.com", 35), ("prositescdn", 40), ("pscdn.net", 30)]),
    ("officite", "Officite", [("officite.com", 35), ("officite", 25)]),
    ("pbhs", "PBHS", [("pbhs.com", 35), ("pbhsinc", 30)]),
    ("sesame", "Sesame Communications", [("sesamecommunications.com", 40), ("sesamehub", 30)]),
    ("patientpop", "PatientPop / Tebra", [("patientpop.com", 40), ("ppopcdn", 35), ("tebra.com", 30)]),
    ("dentalqore", "DentalQore", [("dentalqore", 40)]),
]

# Server-side stacks: what a custom-built site runs on (needle, name)
STACKS: list[tuple[str, str]] = [
    ("__viewstate", "ASP.NET"), ("x-aspnet-version", "ASP.NET"), ("x-powered-by:asp.net", "ASP.NET"),
    ("asp-append-version", "ASP.NET Core"), ("aspnetcdn.com", "ASP.NET"), (".aspx", "ASP.NET"),
    ("server:microsoft-iis", "Microsoft IIS"), ("x-powered-by:php", "PHP"), (".php", "PHP"),
    ('name="csrf-param" content="authenticity_token"', "Ruby on Rails"), ("x-powered-by:next.js", "Next.js"),
    ("bootstrap.min.css", "Bootstrap"), ("/bootstrap/", "Bootstrap"), ("tailwind", "Tailwind CSS"),
    ("jquery", "jQuery"),
]
# A web agency credited on the page ("Website by Acme Digital")
AGENCY_CREDIT = re.compile(
    r"(?:website|site|web design|designed|developed|built|powered|created|marketing)\s+(?:and\s+\w+\s+)?by\s*:?\s*"
    r"(?:<[^>]+>\s*)*([A-Z][\w&.'\- ]{2,40}?)\s*(?:</a>|<|\||\.|,|\n)",
    re.I,
)
BLOCKED = ("cf-chl", "just a moment...", "attention required", "access denied", "verify you are human",
           "captcha", "request unsuccessful", "incapsula", "pardon our interruption")

# Web frameworks: evidence of a custom-built site when no CMS / builder matches
FRAMEWORKS: list[tuple[str, str]] = [
    ("__next_data__", "Next.js"), ("/_next/static/", "Next.js"), ("__nuxt", "Nuxt"), ("/_nuxt/", "Nuxt"),
    ("___gatsby", "Gatsby"), ("ng-version=", "Angular"), ("data-reactroot", "React"), ("laravel_session", "Laravel"),
    ("x-powered-by:express", "Express"), ("csrfmiddlewaretoken", "Django"), ("__sveltekit", "SvelteKit"),
]

# Add-on technologies worth knowing for recommendations: (needle, name, category)
ADDONS: list[tuple[str, str, str]] = [
    # analytics / tracking
    ("googletagmanager.com/gtm.js", "Google Tag Manager", "analytics"), ("gtag/js?id=g-", "Google Analytics 4", "analytics"),
    ("google-analytics.com/analytics.js", "Universal Analytics (retired)", "analytics"),
    ("connect.facebook.net", "Meta Pixel", "analytics"), ("static.hotjar.com", "Hotjar", "analytics"),
    ("clarity.ms", "Microsoft Clarity", "analytics"), ("analytics.tiktok.com", "TikTok Pixel", "analytics"),
    # reviews
    ("judge.me", "Judge.me reviews", "reviews"), ("yotpo", "Yotpo", "reviews"), ("okendo", "Okendo reviews", "reviews"),
    ("loox.io", "Loox reviews", "reviews"), ("stamped.io", "Stamped reviews", "reviews"),
    ("reviews.io", "Reviews.io", "reviews"), ("trustpilot", "Trustpilot widget", "reviews"),
    ("elfsight", "Elfsight widgets", "reviews"),
    # email capture / marketing
    ("klaviyo", "Klaviyo", "email"), ("privy.com", "Privy", "email"), ("omnisend", "Omnisend", "email"),
    ("mailchimp", "Mailchimp", "email"), ("chimpstatic.com", "Mailchimp", "email"), ("mc.us", "Mailchimp", "email"),
    # e-commerce extras
    ("rebuyengine", "Rebuy (recommendations)", "recommendations"), ("product-recommendations", "Product recommendations", "recommendations"),
    ("rechargepayments", "ReCharge subscriptions", "subscriptions"), ("afterpay", "Afterpay", "payments"),
    ("klarna", "Klarna", "payments"), ("shop-pay", "Shop Pay", "payments"), ("paypal", "PayPal", "payments"),
    # chat / booking
    ("widget.intercom.io", "Intercom", "chat"), ("tidio", "Tidio chat", "chat"), ("js.driftt.com", "Drift", "chat"),
    ("livechatinc", "LiveChat", "chat"), ("tawk.to", "Tawk.to chat", "chat"), ("calendly.com", "Calendly", "booking"),
    ("acuityscheduling", "Acuity Scheduling", "booking"), ("squareup.com/appointments", "Square Appointments", "booking"),
    ("setmore", "Setmore", "booking"), ("fresha.com", "Fresha", "booking"),
    # WordPress plugins / builders
    ("yoast", "Yoast SEO", "seo"), ("rank-math", "Rank Math SEO", "seo"), ("all-in-one-seo", "All in One SEO", "seo"),
    ("elementor", "Elementor", "page_builder"), ("/divi/", "Divi", "page_builder"), ("et_pb_", "Divi", "page_builder"),
    ("wpbakery", "WPBakery", "page_builder"), ("contact-form-7", "Contact Form 7", "forms"), ("wpforms", "WPForms", "forms"),
    ("gform_", "Gravity Forms", "forms"), ("wp-rocket", "WP Rocket", "performance"), ("litespeed", "LiteSpeed Cache", "performance"),
    ("jetpack", "Jetpack", "plugins"), ("cloudflare", "Cloudflare", "cdn"),
]


def readable(html: str) -> bool:
    """A real page (not a block / challenge page or an empty shell)."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html or "", flags=re.S | re.I)
    words = len(text.split())
    lower = (html or "").lower()
    return words >= 80 and not (words < 400 and any(b in lower for b in BLOCKED))


def agency_credit(html: str) -> str:
    """The web agency the page credits, e.g. "Webiotic" from "Website by Webiotic" (footer)."""
    tail = (html or "")[-20000:]
    for match in AGENCY_CREDIT.finditer(tail):
        name = re.sub(r"\s+", " ", match.group(1)).strip(" .-|")
        if name and name.lower() not in {"google", "the", "our", "us", "you", "wordpress", "cloudflare"}:
            return name
    hosts = re.findall(r'href=["\']https?://(?:www\.)?([a-z0-9-]+)\.(?:com|net|io|agency|co|design|digital)[/"\']', tail, re.I)
    for host in reversed(hosts[-8:]):  # a footer link to a web company ("webiotic.com")
        if any(w in host.lower() for w in ("web", "design", "digital", "media", "studio", "creative", "marketing", "otic")):
            if not any(s in host.lower() for s in ("facebook", "instagram", "google", "youtube", "linkedin", "twitter")):
                return host
    return ""


def _no_cms(html, html_l, header_l, blob_l, frameworks, secondary, evidence) -> "DetectionResult":
    """No CMS / builder signature: the generator it names, else custom-built (when the page was readable)."""
    generator = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']{2,60})["\']', html, re.I) or re.search(
        r'<meta[^>]+content=["\']([^"\']{2,60})["\'][^>]+name=["\']generator["\']', html, re.I
    )
    if generator:
        name = re.split(r"\s+v?\d", generator.group(1).strip())[0].strip()
        if name:
            conf = 0.7
            evidence.append({"signal": f'generator meta "{generator.group(1)}"', "source": "html", "platform": name})
            tech = [{"name": name, "confidence": conf, "signals": ["generator meta"], "is_primary_platform": True}]
            return DetectionResult(name, conf, ["generator meta"], tech, confidence_label(conf), secondary, evidence)
    stack = []
    for needle, name in STACKS:
        if needle in (header_l if needle.startswith(("x-", "server:")) else blob_l) and name not in stack:
            stack.append(name)
    if not frameworks and not readable(html):
        # Couldn't read the page (blocked or empty): don't guess
        return DetectionResult("Unknown", 0.0, [], [], "Unknown", secondary, evidence)
    built_by = agency_credit(html)
    signals = frameworks + [s for s in stack if s not in frameworks]
    conf = 0.6 if frameworks or {"ASP.NET", "ASP.NET Core", "Ruby on Rails"} & set(stack) else 0.5
    for name in reversed(signals):
        secondary.insert(0, {"name": name, "category": "framework", "signals": [name]})
    evidence += [{"signal": f"{name}", "source": "html", "platform": "Custom-built"} for name in signals]
    evidence.append({"signal": "no CMS or site-builder signature on a readable page", "source": "html", "platform": "Custom-built"})
    if built_by:
        secondary.insert(0, {"name": f"Built by {built_by}", "category": "agency", "signals": ["footer credit"]})
        evidence.append({"signal": f"credits {built_by}", "source": "html", "platform": "Custom-built"})
    tech = [{"name": "Custom-built", "confidence": conf, "signals": signals, "is_primary_platform": True}]
    return DetectionResult("Custom-built", conf, signals, tech, confidence_label(conf), secondary, evidence)


def confidence_label(confidence: float) -> str:
    if confidence >= 0.8:
        return "High"
    if confidence >= 0.5:
        return "Medium"
    if confidence > 0:
        return "Low"
    return "Unknown"


def detect_platform(
    html: str = "",
    headers: dict | None = None,
    cookies: dict | None = None,
    url: str = "",
) -> DetectionResult:
    header_blob = " ".join(f"{k}:{v}" for k, v in (headers or {}).items())
    cookie_blob = " ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    html_l = (html or "").lower()
    header_l = header_blob.lower().replace(": ", ":")
    blob_l = "\n".join([html_l, header_l, cookie_blob.lower(), (url or "").lower()])

    scores: dict[str, int] = {}
    found: dict[str, list[str]] = {}
    evidence: list[dict] = []
    labels = {key: label for key, label, _ in FINGERPRINTS}
    for key, _label, rules in FINGERPRINTS:
        for needle, weight in rules:
            if needle in blob_l:
                scores[key] = scores.get(key, 0) + weight
                found.setdefault(key, []).append(needle)
                evidence.append({"signal": needle, "source": "headers" if needle in header_l else "html", "platform": labels[key]})

    # WooCommerce runs on WordPress
    if scores.get("woocommerce", 0) >= 35:
        scores["wordpress"] = max(scores.get("wordpress", 0), 40)
        found.setdefault("wordpress", []).append("woocommerce-implies-wordpress")

    secondary: list[dict] = []
    seen = set()
    for needle, name, category in ADDONS:
        if needle in blob_l and name not in seen:
            seen.add(name)
            secondary.append({"name": name, "category": category, "signals": [needle]})

    frameworks = sorted({name for needle, name in FRAMEWORKS if needle in blob_l})

    if not scores:
        return _no_cms(html or "", html_l, header_l, blob_l, frameworks, secondary, evidence)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    raw_platform, raw = ranked[0]
    platform = labels.get(raw_platform, raw_platform.title())
    confidence = min(0.99, raw / 100)
    if raw_platform == "shopify" and "x-shopify" in header_l:
        confidence = max(confidence, 0.92)  # Shopify's own response header
    if confidence < 0.3:
        # A single weak signal (e.g. the word "wordpress" in a blog post) is not enough to name the platform
        platform = "Unknown"

    technologies = [
        {
            "name": labels.get(name, name.title()),
            "confidence": min(0.99, value / 100),
            "signals": found.get(name, []),
            "is_primary_platform": name == raw_platform and platform != "Unknown",
        }
        for name, value in ranked
    ]
    for name in frameworks:
        secondary.append({"name": name, "category": "framework", "signals": [name]})
    for name, value in ranked[1:]:
        if value >= 30:
            secondary.insert(0, {"name": labels.get(name, name), "category": "platform", "signals": found.get(name, [])})
    logger.info("platform_detected", platform=platform, confidence=confidence, url=url)
    return DetectionResult(
        platform,
        confidence if platform != "Unknown" else 0.0,
        found.get(raw_platform, []),
        technologies,
        confidence_label(confidence if platform != "Unknown" else 0.0),
        secondary,
        evidence[:30],
    )


def platform_summary(result: DetectionResult, detected_at: str = "") -> dict:
    """What an audit stores about the platform (JSON)."""
    return {
        "primary": result.platform_name,
        "confidence": round(result.confidence, 2),
        "confidence_label": result.confidence_label,
        "secondary": result.secondary[:25],
        "evidence": result.evidence[:30],
        "detected_at": detected_at,
    }


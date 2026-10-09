from typing import Any

from app.logging_config import get_logger
from app.services.scoring import calculate_website_score

logger = get_logger("analysis")


def analyze_website_signals(
    *,
    text: str,
    title: str,
    meta_description: str,
    signals: dict[str, Any],
    pages: list[dict],
    platform: str,
    city: str = "",
    category: str = "",
    business_name: str = "",
) -> dict:
    """Rule-based analysis. Only reports issues supported by observed signals."""
    issues: list[dict] = []
    opportunities: list[dict] = []

    def issue(code: str, title_text: str, detail: str, evidence: str) -> None:
        issues.append({"code": code, "title": title_text, "detail": detail, "evidence": evidence})

    if not signals.get("reachable"):
        issue("unreachable", "Website was not reachable", "The crawler could not load the homepage.", "HTTP request failed or returned an error")
    if signals.get("reachable") and not signals.get("https"):
        issue("no_https", "Site is not served over HTTPS", "The recorded URL does not use HTTPS.", "URL scheme")
    if signals.get("reachable") and not signals.get("title"):
        issue("missing_title", "Missing page title", "The homepage has no title tag.", "HTML <title>")
    if signals.get("reachable") and not signals.get("meta_description"):
        issue("missing_meta", "Missing meta description", "No meta description was found on the homepage.", "HTML meta description")
    if signals.get("reachable") and not signals.get("has_viewport"):
        issue("no_viewport", "No mobile viewport tag", "A viewport meta tag was not detected.", "HTML meta viewport")
        opportunities.append({"code": "mobile", "title": "Improve mobile usability", "detail": "Add a viewport tag and review mobile layout."})
    if signals.get("reachable") and not signals.get("has_cta"):
        issue("weak_cta", "Weak or missing call to action", "No common booking/contact CTA language was found.", "Page text and links")
        opportunities.append({"code": "cta", "title": "Add a visible contact or booking CTA", "detail": "Visitors may not see a clear next step."})
    if signals.get("reachable") and not signals.get("has_booking_cta"):
        opportunities.append({"code": "booking", "title": "Add a booking or appointment CTA", "detail": "No booking/appointment language was detected."})
    if signals.get("reachable") and not signals.get("has_contact_info"):
        issue("poor_contact", "Contact information is hard to find", "No mailto/tel links or contact copy was detected.", "Links and page text")
    if signals.get("reachable") and not signals.get("has_lead_capture"):
        issue(
            "no_lead_capture",
            "No lead capture form detected",
            "Crawled HTML did not include a contact form, quote form, or known form embed (HubSpot, Typeform, Wix, Webflow, and similar).",
            "Forms, inputs, iframes, and embed snippets",
        )
        opportunities.append({"code": "capture", "title": "Add a contact or quote form", "detail": "A form would make it easier for visitors to reach the business."})
    if signals.get("reachable") and not signals.get("has_services_info"):
        issue("missing_services", "Limited service information", "Crawled pages did not clearly describe services.", "Page text")
        opportunities.append({"code": "services", "title": "Describe core services more clearly", "detail": "Service details were not clearly present on crawled pages."})
    if signals.get("reachable") and not signals.get("has_trust_signals"):
        opportunities.append({"code": "trust", "title": "Add trust signals", "detail": "No testimonials, reviews, licensing, or guarantee language was detected."})
    if signals.get("failed_pages"):
        issue("failed_pages", "Some pages failed to load", f"{signals.get('failed_pages')} crawled page(s) returned errors.", "HTTP status")

    services = _extract_list(text, ("services", "we offer", "our services"))
    products = _extract_list(text, ("products", "we sell"))
    business_type = category or _guess_type(text)
    summary_parts = []
    if business_name:
        summary_parts.append(business_name)
    if city:
        summary_parts.append(f"located in {city}")
    if platform and platform not in {"unknown", "custom"}:
        summary_parts.append(f"website appears to be built on {platform.title()}")
    if title:
        summary_parts.append(f"page title: {title}")
    summary = ". ".join(summary_parts) + "." if summary_parts else "Limited public website information was available."

    score, categories = calculate_website_score(signals)
    recommended = _recommend_services(platform, issues)

    raw = {
        "signals": signals,
        "pages": [{"url": p.get("url"), "status": p.get("status_code")} for p in pages],
        "title": title,
        "meta_description": meta_description,
        "platform": platform,
    }
    return {
        "raw_data": raw,
        "summary": summary,
        "business_summary": summary,
        "business_type": business_type,
        "services": services,
        "products": products,
        "main_offering": services[0] if services else (category or ""),
        "target_audience": f"Local customers in {city}" if city else "",
        "location": city,
        "issues": issues,
        "opportunities": opportunities,
        "recommended_services": recommended,
        "category_scores": categories,
        "website_score": score,
    }


def _extract_list(text: str, markers: tuple[str, ...]) -> list[str]:
    if not text:
        return []
    lower = text.lower()
    found: list[str] = []
    for marker in markers:
        idx = lower.find(marker)
        if idx == -1:
            continue
        snippet = text[idx : idx + 280]
        parts = [p.strip(" •-:\n") for p in snippet.replace("\n", " ").split(",") if 2 < len(p.strip()) < 60]
        found.extend(parts[:6])
    # Keep only unique short phrases that look like offerings, not whole paragraphs
    clean = []
    for item in found:
        if item.lower() not in {c.lower() for c in clean} and len(item.split()) <= 8:
            clean.append(item)
    return clean[:8]


def _guess_type(text: str) -> str:
    lowered = (text or "").lower()
    mapping = {
        "plumber": "plumbing",
        "roof": "roofing",
        "dentist": "dental",
        "dental": "dental",
        "hvac": "hvac",
        "lawyer": "legal",
        "restaurant": "restaurant",
    }
    for needle, label in mapping.items():
        if needle in lowered:
            return label
    return ""


def _recommend_services(platform: str, issues: list[dict]) -> list[str]:
    recs: list[str] = []
    codes = {i["code"] for i in issues}
    platform = (platform or "").lower()
    if platform == "shopify":
        recs.append("Shopify Development")
    elif platform in {"wordpress", "woocommerce"}:
        recs.append("WordPress Development")
    elif platform == "wix":
        recs.append("Wix Development")
    elif platform:
        recs.append("Website Design")
    if "no_viewport" in codes or "weak_cta" in codes or "no_lead_capture" in codes:
        recs.append("Website Design")
    if "missing_services" in codes or "missing_meta" in codes:
        recs.append("SEO")
    if "poor_contact" in codes:
        recs.append("Website Maintenance")
    # unique preserve order
    seen = set()
    out = []
    for item in recs:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

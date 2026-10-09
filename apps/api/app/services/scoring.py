from typing import Any


def calculate_website_score(signals: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Score from observed technical/UX signals only. Missing data scores low, not invented issues."""
    technical = 0
    if signals.get("reachable"):
        technical += 10
    if signals.get("https"):
        technical += 8
    if signals.get("title"):
        technical += 4
    if signals.get("meta_description"):
        technical += 3
    if signals.get("has_viewport"):
        technical += 5
    technical = min(technical, 30)

    mobile = 20 if signals.get("has_viewport") else 6
    if signals.get("reachable") and not signals.get("has_viewport"):
        mobile = 4

    cta = 4
    if signals.get("has_cta"):
        cta = 14
    if signals.get("has_booking_cta"):
        cta = 18

    contact = 4
    if signals.get("has_contact_info"):
        contact += 8
    if signals.get("has_email_on_site"):
        contact += 5
    if signals.get("has_contact_page"):
        contact += 3
    contact = min(contact, 20)

    content = 4
    if signals.get("has_services_info"):
        content += 8
    if signals.get("word_count", 0) >= 200:
        content += 4
    if signals.get("has_about"):
        content += 2
    content = min(content, 18)

    conversion = 3
    if signals.get("has_lead_capture"):
        conversion += 6
    if signals.get("has_trust_signals"):
        conversion += 4
    if signals.get("has_cta") and signals.get("has_contact_info"):
        conversion += 3
    conversion = min(conversion, 16)

    design = 8 if signals.get("has_nav") else 3
    if signals.get("has_structure"):
        design += 4
    design = min(design, 12)

    categories = {
        "technical": technical,
        "mobile": mobile,
        "cta": cta,
        "contact": contact,
        "content": content,
        "conversion": conversion,
        "design_ux": design,
    }
    total = min(100, sum(categories.values()))
    return total, categories


def calculate_lead_score(lead: Any, analysis: dict | None = None) -> tuple[int, list[dict]]:
    score = 0
    reasons: list[dict] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append({"points": points, "reason": reason})

    if getattr(lead, "primary_email", ""):
        add(22, "Business email is available")
    else:
        add(0, "No email found")

    if getattr(lead, "website_url", ""):
        add(12, "Website URL is available")
    if getattr(lead, "primary_phone", ""):
        add(8, "Phone number is available")
    if getattr(lead, "address", ""):
        add(5, "Address is available")
    if getattr(lead, "google_place_id", "") or getattr(lead, "google_maps_url", ""):
        add(4, "Maps listing information is available")

    platform = (getattr(lead, "platform", "") or "").lower()
    if platform and platform not in {"", "unknown"}:
        add(8, f"Website platform detected: {platform}")

    website_score = getattr(lead, "website_score", None)
    if website_score is not None:
        # Lower website quality can indicate a genuine improvement opportunity.
        if website_score < 55:
            add(14, "Website score indicates improvement opportunities")
        elif website_score < 75:
            add(8, "Website score shows moderate conversion gaps")
        else:
            add(4, "Website already scores relatively well")

    issues = []
    if analysis:
        issues = analysis.get("issues") or []
    if issues:
        add(min(12, 4 + len(issues) * 2), f"{len(issues)} evidence-based website issues recorded")

    if getattr(lead, "do_not_contact", False):
        add(-40, "Marked do not contact")

    score = max(0, min(100, score))
    return score, reasons

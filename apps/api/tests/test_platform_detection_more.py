"""Platform detection for sites that used to come out "Unknown": custom-built stacks, agency credits, generator
tags, more site builders, and pages that couldn't be read. Plus the PDF's "Your Website Platform" section."""

from datetime import UTC, datetime

from app.services.audit_pdf import build_audit_pdf, platform_intro
from app.services.platform_detection import detect_platform, readable

BODY = "<p>" + " ".join(["Gentle family dentistry in Miami with same day appointments and friendly staff."] * 30) + "</p>"


def page(head="", body=BODY, footer=""):
    return f"<html><head>{head}</head><body>{body}<footer>{footer}</footer></body></html>"


def test_asp_net_site_is_custom_built_not_unknown():
    html = page(head='<script src="//ajax.aspnetcdn.com/ajax/jquery.validate/1.9/jquery.validate.min.js"></script>',
                footer='Website by <a href="https://www.webiotic.com">Webiotic</a>')
    d = detect_platform(html, {"Server": "Microsoft-IIS/10.0"}, url="https://example-dental.com")
    assert d.platform_name == "Custom-built" and d.confidence >= 0.5
    names = [s["name"] for s in d.secondary]
    assert "ASP.NET" in names and "Microsoft IIS" in names and "Built by Webiotic" in names


def test_hand_coded_bootstrap_site():
    d = detect_platform(page(head='<link href="/lib/bootstrap/dist/css/bootstrap.min.css">'), {}, url="https://x.com")
    assert d.platform_name == "Custom-built" and "Bootstrap" in [s["name"] for s in d.secondary]


def test_blocked_or_empty_pages_stay_unknown():
    challenge = "<html><head><title>Just a moment...</title></head><body>Checking your browser cf-chl</body></html>"
    assert detect_platform(challenge, {"server": "cloudflare"}).platform_name == "Unknown"
    assert detect_platform("", {}).platform_name == "Unknown"
    assert not readable(challenge) and readable(page())


def test_generator_tag_names_the_platform():
    d = detect_platform(page(head='<meta name="generator" content="Hugo 0.120.4">'), {})
    assert d.platform_name == "Hugo" and d.confidence >= 0.7


def test_more_site_builders_and_practice_platforms():
    assert detect_platform(page(head='<script src="https://static.tildacdn.com/js/tilda.js"></script>'), {}).platform_name == "Tilda"
    assert detect_platform(page(head='<img src="https://cdn.prositescdn.com/logo.png">'), {}).platform_name == "ProSites"
    assert detect_platform(page(head='<link href="https://assets.zyrosite.com/x.css">'), {}).platform_name == "Hostinger Website Builder"


def test_known_platforms_still_win():
    wp = page(head='<link href="/wp-content/themes/x/style.css"><meta name="generator" content="WordPress 6.5">')
    assert detect_platform(wp, {}).platform_name == "WordPress"


def test_platform_intro_wording():
    assert "runs on WordPress with Elementor and WP Rocket" in platform_intro({"name": "WordPress", "addons": ["Elementor", "WP Rocket"]})
    assert "custom-built by Webiotic" in platform_intro({"name": "Custom-built", "built_by": "Webiotic"})
    assert "couldn't confirm" in platform_intro({"name": "Unknown"})


def test_pdf_has_the_platform_section():
    report = {
        "overall_score": 60, "findings": [], "platform": "WordPress",
        "platform_opportunities": [{
            "title": "Switch off Elementor entrance animations", "status": "improvement",
            "evidence": "Sections below the hero load blank.", "recommendation": "Set Motion Effects to None.",
            "business_relevance": "Content shows straight away.",
        }],
    }
    pdf = build_audit_pdf(report=report, performance={"url": "https://x.com"}, lead={"business_name": "X Dental"},
                          sender={"company_name": "Bee Creative"}, generated_at=datetime.now(UTC),
                          platform={"name": "WordPress", "addons": ["Elementor"]})
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000

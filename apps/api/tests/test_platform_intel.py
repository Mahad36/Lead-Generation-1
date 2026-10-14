import json
from types import SimpleNamespace

import pytest

from app.services import platform_checks
from app.services.platform_checks import common_checks, ecommerce_checks, page_unreadable, run_platform_checks
from app.services.platform_detection import detect_platform


def page(head="", body="", generator=""):
    gen = f'<meta name="generator" content="{generator}">' if generator else ""
    return f"<html><head>{gen}{head}</head><body>{body}</body></html>"


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize(
    "html,headers,expected",
    [
        (page(body='<script src="https://cdn.shopify.com/s/files/x.js"></script><div class="shopify-section"></div>'), {}, "Shopify"),
        (page(generator="WordPress 6.5", body='<link href="/wp-content/themes/a/style.css"><script src="/wp-includes/js/x.js"></script>'), {}, "WordPress"),
        (page(body='<img src="https://static.wixstatic.com/media/a.jpg">'), {"x-wix-request-id": "1"}, "Wix"),
        (page(body='<link href="https://static1.squarespace.com/static/a.css">'), {}, "Squarespace"),
        (page(head='<html data-wf-site="123">', body='<script src="https://assets.website-files.com/a.js"></script>'), {}, "Webflow"),
        (page(body='<script>require(["Magento_Ui/js/x"])</script><script src="/static/version123/mage/cookies.js"></script>'), {}, "Magento / Adobe Commerce"),
        (page(body='<script src="https://cdn11.bigcommerce.com/s-abc/stencil-utils.js"></script>'), {}, "BigCommerce"),
        (page(generator="PrestaShop", body="<script>var prestashop = {}</script>"), {}, "PrestaShop"),
    ],
)
def test_platforms(html, headers, expected):
    result = detect_platform(html=html, headers=headers, url="https://example.com")
    assert result.platform_name == expected
    assert result.confidence_label in {"High", "Medium"}
    assert result.evidence  # what was observed is kept


def test_mixed_stack_woocommerce_on_wordpress_with_addons():
    html = page(
        body='<link href="/wp-content/plugins/woocommerce/assets/a.css"><script src="/wp-includes/x.js"></script>'
        '<script src="https://static.klaviyo.com/onsite/js/klaviyo.js"></script><div class="jdgm-widget judge.me"></div>'
        '<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC"></script>'
    )
    result = detect_platform(html=html, url="https://shop.example")
    assert result.platform_name == "WooCommerce"
    names = {s["name"] for s in result.secondary}
    assert {"WordPress", "Klaviyo", "Judge.me reviews", "Google Analytics 4"} <= names


def test_uncertain_and_custom():
    assert detect_platform(html="").platform_name == "Unknown"
    # one weak mention of WordPress in a blog post is not enough to name the platform
    weak = detect_platform(html=page(body="<p>We moved off wordpress last year.</p>"))
    assert weak.platform_name == "Unknown" and weak.confidence_label == "Unknown"
    custom = detect_platform(html=page(body='<script id="__NEXT_DATA__" type="application/json">{}</script>'))
    assert custom.platform_name == "Custom-built" and any(s["name"] == "Next.js" for s in custom.secondary)
    plain = detect_platform(html=page(body="<h1>Hello</h1>"), url="https://custom.example/about")
    assert plain.platform_name == "Unknown"  # no signature: not guessed


# ---------------------------------------------------------------- evidence-based checks

HOME = "https://shop.example/"


def test_confirmed_missing_only_with_evidence():
    html = page(head="<title>Acme Plumbing | Emergency plumber in Miami</title>", body="<p>Hi</p>")
    checks = {c["id"]: c for c in common_checks(HOME, html, [(HOME, html)], [])}
    assert checks["meta_description"]["status"] == "confirmed_missing"
    assert checks["meta_description"]["evidence"][0]["url"] == HOME
    assert checks["viewport"]["status"] == "confirmed_missing"
    assert checks["title"]["status"] == "present"
    # scripts / apps can be invisible: never "missing", only "not detected"
    assert checks["analytics"]["status"] == "not_detected"
    assert checks["lead_form"]["status"] == "not_detected"


def test_third_party_integrations_count_as_present():
    html = page(
        head='<meta name="viewport" content="width=device-width"><meta name="description" content="' + "x" * 130 + '">',
        body='<form><input type="email" name="email"></form><a href="tel:+1555">Call</a>',
    )
    secondary = [{"name": "Hotjar", "category": "analytics", "signals": []}]
    checks = {c["id"]: c for c in common_checks(HOME, html, [(HOME, html)], secondary)}
    assert checks["analytics"]["status"] == "present"
    assert checks["lead_form"]["status"] == "present"
    assert checks["click_to_call"]["status"] == "present"


class FakeResponse:
    def __init__(self, status, text, url, ctype="text/html"):
        self.status_code, self.text, self.url = status, text, url
        self.headers = {"content-type": ctype}


class FakeClient:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url):
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response
        return FakeResponse(404, "", url)


def test_shopify_product_page_checks():
    product = page(
        head='<script type="application/ld+json">{"@type": "Product", "name": "Mug"}</script>',
        body='<div class="loox-rating"></div><product-recommendations></product-recommendations>',
    )
    client = FakeClient(
        {
            "https://shop.example/products.json": FakeResponse(200, json.dumps({"products": [{"handle": "mug"}]}), "https://shop.example/products.json", "application/json"),
            "https://shop.example/products/mug": FakeResponse(200, product, "https://shop.example/products/mug"),
        }
    )
    secondary = [{"name": "Loox reviews", "category": "reviews", "signals": []}]
    checks = {c["id"]: c for c in ecommerce_checks(client, "Shopify", HOME, page(), [(HOME, page())], secondary)}
    assert checks["product_schema"]["status"] == "present"
    assert checks["reviews"]["status"] == "present"  # a review app, not "missing"
    assert checks["recommendations"]["status"] == "present"
    assert checks["email_capture"]["status"] == "not_detected"


def test_product_page_without_schema_is_confirmed_missing_and_unavailable_page_needs_review():
    client = FakeClient(
        {
            "https://shop.example/products.json": FakeResponse(200, json.dumps({"products": [{"handle": "mug"}]}), "x", "application/json"),
            "https://shop.example/products/mug": FakeResponse(200, page(body="<h1>Mug</h1>"), "https://shop.example/products/mug"),
        }
    )
    checks = {c["id"]: c for c in ecommerce_checks(client, "Shopify", HOME, page(), [(HOME, page())], [])}
    assert checks["product_schema"]["status"] == "confirmed_missing"
    assert checks["reviews"]["status"] == "not_detected"  # could be a script: not claimed missing
    down = FakeClient({})
    checks = {c["id"]: c for c in ecommerce_checks(down, "Shopify", HOME, page(), [(HOME, page())], [])}
    assert checks["product_page"]["status"] == "manual_review"


def test_unreadable_site_invents_nothing(monkeypatch):
    blocked = "<html><head><title>Just a moment...</title></head><body>cf-chl challenge</body></html>"
    assert page_unreadable(blocked)
    assert page_unreadable("", 403)
    detection = SimpleNamespace(platform_name="Unknown", secondary=[])
    result = run_platform_checks(HOME, [(HOME, blocked)], detection)
    assert [c["status"] for c in result["checks"]] == ["manual_review"]
    assert result["counts"]["confirmed_missing"] == 0


def test_report_is_kept_honest_against_the_checks():
    from app.services.website_audit import apply_platform_results

    audit = SimpleNamespace(
        site_data={
            "platform_intel": {
                "primary": "Shopify",
                "confidence": 0.9,
                "checks": [
                    {"feature": "Product reviews", "status": "present", "evidence": [{"url": HOME}]},
                    {"feature": "XML sitemap", "status": "confirmed_missing", "evidence": [{"url": HOME + "sitemap.xml"}]},
                ],
            },
            "pages_crawled": [{"url": HOME}],
        },
        website_url=HOME,
        platform="",
        platform_confidence=None,
        finding_types=[],
        outreach_angle="",
    )
    report = {
        "findings": [{"title": "Product reviews are missing", "status": "confirmed_missing", "evidence_url": HOME, "where": "Product page"}],
        "platform_opportunities": [{"title": "Add a sitemap", "status": "confirmed_missing", "evidence_url": HOME + "sitemap.xml"}],
        "outreach_angle": "While reviewing your Shopify store...",
    }
    lead = SimpleNamespace(platform="")
    apply_platform_results(audit, lead, report)
    assert report["findings"][0]["status"] == "manual_review"  # a check saw reviews: not "missing"
    assert report["platform_opportunities"][0]["status"] == "confirmed_missing"
    assert audit.platform == "Shopify" and lead.platform == "Shopify"
    assert "confirmed_missing" in audit.finding_types and audit.outreach_angle.startswith("While reviewing")


def test_checks_never_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(platform_checks, "crawl_files_checks", boom)
    html = page(head="<title>A good long homepage title here</title>", body="<h1>x</h1>" + "<p>x</p>" * 200)
    result = run_platform_checks(HOME, [(HOME, html)], SimpleNamespace(platform_name="Unknown", secondary=[]))
    assert any(c["id"] == "checks_error" for c in result["checks"])

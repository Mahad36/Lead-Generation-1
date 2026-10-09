from pathlib import Path
from urllib.parse import parse_qs, urlparse
import re

from app.services.email_extraction import extract_emails_from_html
from app.services.platform_detection import detect_platform
from app.services.scraper import _clean_maps_phone, _clean_maps_website, parse_card, short_browser_error


def build_maps_query(country: str, city: str, category: str) -> str:
    return f"{category} in {city}, {country}"


def _place_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "cid" in qs:
        return qs["cid"][0]
    match = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", url)
    return match.group(1) if match else ""

FIXTURE = Path(__file__).parent / "fixtures" / "sample_business.html"


def test_maps_query():
    assert build_maps_query("USA", "Miami", "Plumbers") == "Plumbers in Miami, USA"


def test_place_id_from_cid():
    assert _place_id_from_url("https://www.google.com/maps?cid=12345") == "12345"


def test_fixture_html_extraction():
    html = FIXTURE.read_text(encoding="utf-8")
    emails = extract_emails_from_html(html, "https://abc-plumbing.example/contact", "abc-plumbing.example")
    assert any(e["email"] == "info@abc-plumbing.example" for e in emails)
    platform = detect_platform(html=html, url="https://abc-plumbing.example")
    assert platform.platform_name == "Wix"


def test_maps_phone_and_website_cleanup():
    assert _clean_maps_phone("Phone: +1 305-661-7838") == "+1 305-661-7838"
    assert _clean_maps_phone("tel:+13056617838") == "+13056617838"
    assert _clean_maps_website("rotorooter.com") == "https://rotorooter.com"
    assert _clean_maps_website("https://www.google.com/maps") == ""


def test_browser_errors_are_human_readable():
    message = short_browser_error(Exception("Timeout 45000ms exceeded.\nCall log:\n  - navigating"))
    assert "too long" in message and "Call log" not in message
    assert "setup" in short_browser_error(Exception("Executable doesn't exist at C:/ms-playwright/chromium"))


ROTO_CARD = {
    "name": "Roto-Rooter Plumbing & Water Cleanup",
    "href": "https://www.google.com/maps/place/Roto/data=!4m7!3m6!1s0x88d9b0d155555555:0x5863a3c0f691077a"
    "!8m2!3d25.8092!4d-80.2361!16s?entry=ttu",
    "text": "Roto-Rooter Plumbing & Water Cleanup\nRoto-Rooter Plumbing & Water Cleanup\n4.7\n"
    "Plumber \u00b7 1726 NW 36th St Unit 3\nOpen 24 hours \u00b7 +1 305-661-7838\n\ue80b\nWebsite\n\ue52e\nDirections",
    "website": "https://www.rotorooter.com/miami/?utm_source=Google&utm_medium=organic",
    "stars": "4.7 stars",
    "booking": "",
}


def test_parse_card_reads_list_details():
    b = parse_card(ROTO_CARD, "Plumbers")
    assert (b.business_name, b.category, b.address) == (
        "Roto-Rooter Plumbing & Water Cleanup",
        "Plumber",
        "1726 NW 36th St Unit 3",
    )
    assert b.phone == "+1 305-661-7838" and b.rating == 4.7
    assert b.website == "https://www.rotorooter.com/miami/"
    assert b.google_place_id == "0x88d9b0d155555555:0x5863a3c0f691077a"
    assert b.extra["hours"] == "Open 24 hours" and b.extra["latitude"] == 25.8092
    assert parse_card({"name": "", "href": ROTO_CARD["href"]}, "x") is None


def test_parse_card_without_address_or_website():
    card = {
        "name": "Main Plumbing",
        "href": "https://www.google.com/maps/place/Main/data=!1s0x1:0x2",
        "text": "Main Plumbing\n4.9\nPlumber\nClosed \u22c5 Opens 8 AM \u00b7 +1 786-269-3783",
        "website": "",
        "stars": "",
    }
    b = parse_card(card, "Plumbers")
    assert (b.category, b.address, b.website, b.phone) == ("Plumber", "", "", "+1 786-269-3783")
    assert b.extra["hours"].startswith("Closed")


def test_cloudflare_email_and_placeholders():
    from app.services.email_extraction import decode_cloudflare_email
    from app.utils.emails import is_plausible_email

    # "a@b.co" XOR-ed with 0x42, as Cloudflare's email protection does
    encoded = "42" + "".join(f"{ord(c) ^ 0x42:02x}" for c in "a@b.co")
    assert decode_cloudflare_email(encoded) == "a@b.co"
    html = f'<a href="/cdn-cgi/l/email-protection#{encoded}">[email&#160;protected]</a>'
    assert [e["email"] for e in extract_emails_from_html(html)] == ["a@b.co"]
    assert not is_plausible_email("example@mysite.com")
    assert not is_plausible_email("john.doe@gmail.com")



def test_needs_browser_only_for_firewall_refusals():
    from app.services.scraper import needs_browser

    assert needs_browser({"website_error": "The website answered HTTP 403"})
    assert needs_browser({"website_error": "The website answered HTTP 503"})
    assert not needs_browser({"website_error": "The website answered HTTP 404"})
    assert not needs_browser({"email": "a@b.co", "website_error": "The website answered HTTP 403"})
    assert not needs_browser({})


def test_dns_fallback_used_only_when_system_dns_fails(monkeypatch):
    import socket

    from app.utils import dns_fallback

    calls = []

    def system(host, port, family=0, type=0, proto=0, flags=0):
        calls.append(host)
        if host == "broken.example.org":
            raise socket.gaierror(11002, "getaddrinfo failed")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host if host[0].isdigit() else "1.2.3.4", port))]

    monkeypatch.setattr(dns_fallback, "_original_getaddrinfo", system)
    monkeypatch.setattr(dns_fallback, "resolve_over_https", lambda host: ["93.184.216.34"])
    assert dns_fallback._getaddrinfo("ok.example.org", 443)[0][4] == ("1.2.3.4", 443)
    assert dns_fallback._getaddrinfo("broken.example.org", 443)[0][4] == ("93.184.216.34", 443)
    monkeypatch.setattr(dns_fallback, "resolve_over_https", lambda host: [])
    try:
        dns_fallback._getaddrinfo("broken.example.org", 443)
        raise AssertionError("expected gaierror")
    except socket.gaierror:
        pass

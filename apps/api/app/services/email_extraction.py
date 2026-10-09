import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from app.utils.emails import choose_primary_email, extract_emails_from_text, is_plausible_email


_CF_EMAIL = re.compile(r"(?:data-cfemail=[\"']|/cdn-cgi/l/email-protection#)([0-9a-fA-F]{6,})")


def decode_cloudflare_email(encoded: str) -> str:
    """Cloudflare "email protection" hides addresses as hex XOR-ed with the first byte."""
    try:
        data = bytes.fromhex(encoded)
        return "".join(chr(b ^ data[0]) for b in data[1:])
    except (ValueError, IndexError):
        return ""


def extract_emails_from_html(html: str, page_url: str = "", domain: str = "") -> list[dict]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, str] = {}

    for encoded in _CF_EMAIL.findall(html):
        address = decode_cloudflare_email(encoded).strip()
        if is_plausible_email(address):
            found[address.lower()] = page_url

    for mailto in soup.select("a[href^=mailto]"):
        href = unquote(mailto.get("href", ""))
        address = href.replace("mailto:", "", 1).split("?", 1)[0].strip()
        if is_plausible_email(address):
            found[address.lower()] = page_url

    text_chunks = [soup.get_text(" ", strip=True)]
    for script in soup.find_all("script", type=lambda t: t in {"application/ld+json", "application/json"}):
        if script.string:
            text_chunks.append(script.string)
    for email in extract_emails_from_text("\n".join(text_chunks)):
        found.setdefault(email, page_url)

    emails = list(found.keys())
    primary = choose_primary_email(emails, domain)
    return [
        {"email": email, "source_url": found[email], "is_primary": email == primary}
        for email in emails
    ]

import re
from urllib.parse import urlparse

import tldextract

# Use the bundled public-suffix list: no network fetch on first use (works offline, no proxy errors).
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

_NAME_STRIP = re.compile(
    r"\b(llc|inc|incorporated|ltd|limited|corp|corporation|co|company|plc|llp|pc|pllc)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
_PHONE_DIGITS = re.compile(r"\D+")


def normalize_business_name(name: str) -> str:
    value = (name or "").strip().lower()
    value = _NAME_STRIP.sub("", value)
    value = _PUNCT.sub(" ", value)
    value = _WS.sub(" ", value).strip()
    return value


def normalize_address(address: str) -> str:
    value = (address or "").strip().lower()
    value = value.replace(",", " ")
    value = _PUNCT.sub(" ", value)
    value = _WS.sub(" ", value).strip()
    return value


def normalize_phone(phone: str) -> str:
    digits = _PHONE_DIGITS.sub("", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def extract_domain(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    extracted = _EXTRACT(raw)
    if not extracted.domain:
        parsed = urlparse(raw)
        return (parsed.netloc or "").lower().removeprefix("www.")
    suffix = f".{extracted.suffix}" if extracted.suffix else ""
    return f"{extracted.domain}{suffix}".lower()


def normalize_url(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw

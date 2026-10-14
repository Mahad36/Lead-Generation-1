"""How every email starts: "Hi <owner's first name>," when the owner (or the person it goes to) is known,
otherwise "Hi there,". Never the business name ("Hi ABC Plumbing,") and never "Hi team".

The name comes from, in order: the contact the email is addressed to (found on the website with their name),
an owner / founder / director named on the website, the lead's contact name. Anything that doesn't look like a
person's first name (a business word, the business's own name, an initial) is ignored.
"""

from __future__ import annotations

import re

NOT_FIRST_NAMES = {
    "info", "team", "office", "sales", "the", "contact", "admin", "customer", "service", "services", "support",
    "hello", "hi", "dear", "sir", "madam", "owner", "manager", "reception", "bookings", "accounts", "enquiries",
    "mr", "mrs", "ms", "dr", "company", "group", "shop", "store", "studio", "and", "of",
}
SENIOR = ("owner", "founder", "ceo", "director", "president", "principal", "proprietor", "partner", "inhaber")
GREETING_RE = re.compile(
    r"^(\s*(?:<p[^>]*>|<div[^>]*>)?\s*)(hi|hello|hey|dear|good (?:morning|afternoon|evening)|greetings)\b([^<\n,!]{0,60})([,!]?)",
    re.I,
)


def clean_first_name(name: str | None, business_name: str = "") -> str:
    """"Sarah Mitchell" -> "Sarah"; "" when it doesn't look like a person's first name."""
    words = re.findall(r"[A-Za-zÀ-ÿ'’-]+", name or "")
    if not words:
        return ""
    first = words[0].strip("'’-")
    business_words = {w.lower() for w in re.findall(r"[A-Za-z]+", business_name or "")}
    if len(first) < 2 or first.lower() in NOT_FIRST_NAMES or first.lower() in business_words:
        return ""
    if first.isupper() and len(first) > 3:
        first = first.title()
    return first[0].upper() + first[1:]


def owner_first_name(lead, recipient_email: str | None = None) -> str:
    """The first name to greet for this lead's email ("" = greet with "Hi there,")."""
    if lead is None:
        return ""
    business = lead.business_name or ""
    emails = list(getattr(lead, "emails", None) or [])
    recipient = (recipient_email or getattr(lead, "primary_email", "") or "").lower()
    for row in emails:  # the person the email actually goes to
        if row.email.lower() == recipient and row.contact_name:
            name = clean_first_name(row.contact_name, business)
            if name:
                return name
    for row in emails:  # an owner / founder / director named on the website
        if row.contact_name and any(s in (row.job_title or "").lower() for s in SENIOR):
            name = clean_first_name(row.contact_name, business)
            if name:
                return name
    return clean_first_name(getattr(lead, "contact_name", ""), business)


def greeting_line(first_name: str) -> str:
    return f"Hi {first_name}," if first_name else "Hi there,"


def apply_greeting(html: str, first_name: str) -> str:
    """Make the email's opening greeting "Hi <first name>," or "Hi there," (adds one when the email has none)."""
    if not html:
        return html
    line = greeting_line(first_name)
    match = GREETING_RE.match(html)
    if match:
        return f"{match.group(1)}{line}{html[match.end():]}"
    return f"<p>{line}</p>{html}"


def apply_greeting_text(text: str, first_name: str) -> str:
    """The same for a plain-text body."""
    if not text:
        return text
    line = greeting_line(first_name)
    match = GREETING_RE.match(text)
    if match:
        return f"{match.group(1)}{line}{text[match.end():]}"
    return f"{line}\n\n{text}"

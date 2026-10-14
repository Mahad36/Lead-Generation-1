"""Signature placement for outgoing emails.

The signature itself comes from each Gmail account (services/gmail_signature.py); nothing is written or stored in
the app. These helpers only put that account's signature in the right place and avoid a doubled name.
"""

from __future__ import annotations

import re
from html import escape

CLOSINGS = r"(best|thanks|cheers|regards|kind regards|best regards|thank you|talk soon|speak soon)[,!.]?"


def append_signature(html_body: str, text_body: str, sig_html: str, sig_text: str) -> tuple[str, str]:
    """The email with the account's signature under it (unchanged when the account has none)."""
    if not sig_html:
        return html_body, text_body
    if text_body.strip() and sig_text:
        text = f"{text_body.rstrip()}\n\n{sig_text}"
    else:
        text = text_body or sig_text
    return f"{html_body}{sig_html}", text


def ensure_sign_off(body_html: str, sender_name: str, company_name: str) -> str:
    """No signature to add: make sure a bare closing ("Best,") is followed by the sender's name and company."""
    names = "<br>".join(escape(n.strip()) for n in (sender_name, company_name) if n and n.strip())
    match = re.search(r"<p\b[^>]*>\s*([A-Za-z ,!.]{2,30})\s*</p>\s*$", body_html or "")
    if not names or not match or not re.fullmatch(CLOSINGS, match.group(1).strip(), re.I):
        return body_html
    return f"{body_html[: match.end(1)]}<br>{names}{body_html[match.end(1):]}"


def insert_signature(html_body: str, signature: str, sender_name: str, company_name: str) -> str:
    """Put the signature at the end of a template email, above its Unsubscribe line, replacing name/company lines."""
    if not signature:
        return html_body
    match = re.search(r"<p\b[^>]*>(?:(?!<p\b).)*unsubscribe(?:(?!<p\b).)*</p>\s*$", html_body or "", re.I | re.S)
    body, tail = (html_body[: match.start()], html_body[match.start() :]) if match else (html_body or "", "")
    return f"{drop_ai_sign_off(body.rstrip(), sender_name, company_name)}{signature}{tail}"


def drop_ai_sign_off(body_html: str, sender_name: str, company_name: str) -> str:
    """Remove the name/company lines at the end of an email, since the signature now carries them.

    Only the closing is touched, and only lines that are just the name and/or company ("Mahad",
    "Bee Creatives", "Mahad, Bee Creatives"), in one paragraph or one per paragraph; a closing like "Best," stays.
    """
    while True:
        trimmed = _drop_sign_off_once(body_html, sender_name, company_name)
        if trimmed == body_html:
            return body_html
        body_html = trimmed


def _drop_sign_off_once(body_html: str, sender_name: str, company_name: str) -> str:
    names = [n.strip() for n in (sender_name, company_name) if n and n.strip()]
    if not names or not body_html:
        return body_html
    match = re.search(r"<p\b[^>]*>((?:(?!<p\b).)*?)</p>\s*$", body_html, re.I | re.S)
    if not match or len(re.sub(r"<[^>]+>", "", match.group(1))) > 160:
        return body_html
    kept = []
    for part in re.split(r"<br\s*/?>", match.group(1), flags=re.I):
        plain = re.sub(r"<[^>]+>", "", part)
        rest = plain
        for name in names:
            rest = re.sub(re.escape(name), "", rest, flags=re.I)
        rest = re.sub(r"\b(?:from|at|of|with)\b", "", rest, flags=re.I)
        if plain.strip() and not re.sub(r"[\s,.\-–—|&]+", "", rest):
            continue  # this line was only the name / company
        for name in names:  # "Best, Mahad" -> "Best,"
            if re.search(r"\s" + re.escape(name) + r"\s*$", plain, re.I):
                part = re.sub(r"\s*" + re.escape(name) + r"\s*$", "", plain, flags=re.I)
                break
        kept.append(part.strip())
    kept = [k for k in kept if k]
    head = body_html[: match.start()]
    return f"{head}<p>{'<br>'.join(kept)}</p>" if kept else head.rstrip()

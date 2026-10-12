"""The email signature from Settings (plain text, one line per row), added under every email you send.

Kept as plain text in the email (no images or tables) so it looks like a person's Gmail signature and doesn't push
cold emails towards the Promotions tab. Web addresses and email addresses become links.
"""

from __future__ import annotations

import re
from html import escape

LINK_RE = re.compile(
    r"(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
    r"|(?P<url>(?:https?://|www\.)[^\s<>\"']+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|co|agency|studio|design|dev|"
    r"uk|pk|de|ca|au|us|ae|in|me|app|site|online|store|shop)(?:/[^\s<>\"']*)?\b)",
    re.I,
)


def signature_lines(signature: str | None) -> list[str]:
    lines = [line.rstrip() for line in (signature or "").strip().splitlines()]
    while lines and lines[0].strip() in ("--", "-- "):
        lines.pop(0)  # the app adds the spacing; a typed "--" delimiter is not needed
    return lines


def _linked(line: str) -> str:
    out, last = [], 0
    for match in LINK_RE.finditer(line):
        out.append(escape(line[last : match.start()]))
        text = match.group(0).rstrip(".,;:)")
        tail = match.group(0)[len(text) :]
        if match.group("email"):
            href = f"mailto:{text}"
        else:
            href = text if text.lower().startswith("http") else f"https://{text}"
        out.append(f'<a href="{escape(href, quote=True)}">{escape(text)}</a>{escape(tail)}')
        last = match.end()
    out.append(escape(line[last:]))
    return "".join(out)


def signature_html(signature: str | None) -> str:
    lines = signature_lines(signature)
    if not lines:
        return ""
    return "<p>" + "<br>".join(_linked(line) for line in lines) + "</p>"


def signature_text(signature: str | None) -> str:
    return "\n".join(signature_lines(signature))


def append_signature(html_body: str, text_body: str, signature: str | None) -> tuple[str, str]:
    """The email with the signature under it (unchanged when no signature is set)."""
    html = signature_html(signature)
    if not html:
        return html_body, text_body
    text = signature_text(signature)
    return f"{html_body}{html}", (f"{text_body.rstrip()}\n\n{text}" if text_body.strip() else text)


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

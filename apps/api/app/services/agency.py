"""Agency outreach: white-label partnership pitches to agencies (e.g. US Shopify and marketing agencies).

An agency file is an ordinary scrape file with purpose "agency". Its leads go through the same pipeline as audit
outreach (address checks, inbox rotation, health protection, sending hours, follow-ups, replies), but instead of a
CRO audit each agency gets a short pitch Claude writes from its own website and your "What you offer agencies"
notes, with your company profile PDF attached (Agency outreach tab). No speed test, screenshots or audit PDF.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import UserSettings
from app.services.ai import AIProvider, AIProviderError

PROFILE_MAX_BYTES = 10 * 1024 * 1024  # Gmail allows 25 MB per email; keep the profile well under it
DEFAULT_PROFILE_NAME = "company-profile.pdf"


def profile_size(db: Session, user_id: str) -> int:
    """Size of your uploaded company profile PDF (0: none), without loading the file."""
    return int(
        db.query(func.coalesce(func.octet_length(UserSettings.agency_profile_pdf), 0))
        .filter(UserSettings.user_id == user_id)
        .scalar()
        or 0
    )


def profile_attachment(db: Session, user_id: str) -> tuple[bytes, str] | None:
    """(pdf, filename) of your current company profile; the latest upload is what each email sends."""
    row = (
        db.query(UserSettings.agency_profile_pdf, UserSettings.agency_profile_filename)
        .filter(UserSettings.user_id == user_id)
        .first()
    )
    if not row or not row[0]:
        return None
    return bytes(row[0]), row[1] or DEFAULT_PROFILE_NAME


_HEADING = re.compile(r"^\s*([A-Za-z][A-Za-z ]{1,40}):\s*$")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/[^\s<>\"')]*)?", re.I)


def links_from_notes(notes: str) -> list[str]:
    """URLs in the "Links" section of your offer notes: put in every email as they are (never click-tracked)."""
    links: list[str] = []
    in_section = False
    for line in (notes or "").splitlines():
        heading = _HEADING.match(line)
        if heading:
            in_section = heading.group(1).strip().lower().startswith("links")
            continue
        if in_section:
            for raw in _URL.findall(line):
                url = raw.rstrip(".,;")
                url = url if url.lower().startswith("http") else f"https://{url}"
                if url not in links:
                    links.append(url)
    return links[:5]


def ensure_links(body: str, links: list[str]) -> str:
    """Every "Links" URL is in the email: any Claude left out is added before the sign-off."""
    missing = [u for u in links if u.rstrip("/").lower() not in body.lower()]
    if not missing:
        return body
    line = "<p>" + " · ".join(f'<a href="{u}">{u}</a>' for u in missing) + "</p>"
    closing = body.rfind("<p>")
    tail = body[closing:] if closing >= 0 else ""
    if closing >= 0 and len(re.sub(r"<[^>]+>", "", tail).strip()) <= 20:  # a short "Best," sign-off
        return body[:closing] + line + tail
    return body + line


def pitch_notes(db: Session, user_id: str) -> str:
    return (db.query(UserSettings.agency_pitch).filter(UserSettings.user_id == user_id).scalar() or "").strip()


AGENCY_EMAIL_SYSTEM = """You write short, persuasive cold emails from a digital agency to another agency (a Shopify,
ecommerce, web or marketing agency, often abroad), offering to be its white-label delivery team: the sender's team
does the work behind the scenes and the agency delivers it to its own clients under its own brand, at its own price.
The sender's company profile PDF is attached. The goal is a reply.

This is an introduction, not a reply to something on the agency's website: never say or imply you looked at the
agency's site, work, clients or niche, and never guess what they specialise in.

What makes agency owners reply: more capacity without hiring, and more margin on every project. Lead with that, not
with who we are.

Structure (in this order, each part short):
1. Greeting: "Hi <agency.greeting_name>," when greeting_name is given; otherwise exactly "Hi there,". Never greet the
   agency name or "team".
2. Hook, one sentence, about the agency's world, not about us. Pick ONE angle at random and word it freshly each
   time (never copy these words): a busy month with more client work than the team can take; a client asking for a
   service the agency doesn't do in-house (an app, SEO, ads); hiring developers being slow and expensive; margins
   getting squeezed on fixed-price builds; a deadline that needs extra hands for a few weeks. A short question works
   well. It must not claim anything about this particular agency.
3. The offer, 1-2 sentences: we become their behind-the-scenes delivery team, under their brand, never in touch with
   their clients, and they keep the client and set the price. Include every "Always mention" point here (see below),
   framed as the agency's gain (e.g. lower delivery cost means more margin on each project). You may use the agency's
   name (agency.name) once.
4. Proof, one sentence, with the strongest credentials from offer.notes (e.g. partner badge, projects delivered, team
   size, years). Then the "Links" URLs (see below) on their own short line, e.g. "Shopify Partner profile: URL ·
   Website: URL".
5. What we can take on: a compact bulleted list (<ul><li>) of 4-6 items covering all the main services in
   offer.notes, each item starting with a short bold label, e.g. "<b>Websites:</b> Shopify, WordPress, Webflow,
   Squarespace, Showit, custom". Group related services so nothing in offer.notes is left out.
6. One short line: the company profile is attached (portfolio and case studies inside).
7. Call to action that is easy to act on, e.g. "Got a project you're quoting right now? Send it over and we'll
   price it." or "Worth a 15-minute call this week?" Then sign off: when sender.signature_added is true, only a
   short closing such as "Best," with nothing after it (the signature is added underneath automatically); otherwise
   "Best," and the company name on the next line.

Rules:
- Write as the company: "we", "our team", "Bee Creative" (sender.company_name). Never name a person and never write
  "I'm <name>": the email can go out from any of several inboxes, and the sending inbox's own signature (with the
  right person's name) is added underneath.
- Only state services, prices, turnaround times, time zones, guarantees or numbers that appear in offer.notes; never
  invent any. No made-up percentages or savings figures.
- "Always mention": when offer.notes has an "Always mention" section, include every point in it in every email, as
  natural sentences in your own words.
- "Links": when offer.notes has a "Links" section, include every link in it in every email, once each, written out
  in full as its own visible link text (<a href="URL">URL</a>) with a few words saying what it is, e.g. "see our
  Shopify Partner Directory profile: <a href="URL">URL</a>". These are the only links in the email: no portfolio
  links (the attached profile shows the work). Return an empty reference_sites list.
- Peer to peer tone: confident, a potential partner, not a vendor begging for work. No flattery, no fake urgency.
- 90-140 words including the list. body_html: <p> paragraphs, the one <ul> list and <b> labels only (no <html>,
  <head>, styles or images), no unsubscribe line, no P.S.
- subject: 3-7 words, under 50 characters, written to get opened: about their gain, like a person's note
  (lower-case is fine), e.g. "extra dev team for Acme?", "more margin on your Shopify builds", "white-label partner
  for Acme". Never use "free", "guaranteed", "cheap", "boost", exclamation marks or ALL CAPS in it.
"""

AS_THE_COMPANY = """- For this email the voice is the company's, not one consultant's: "we" and "our team" instead of "I"; nothing
  about having looked at the recipient's website.
"""

AGENCY_EMAIL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body_html": {"type": "string"},
        "reference_sites": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "body_html", "reference_sites"],
    "additionalProperties": False,
}


def agency_context(lead: dict, site: dict) -> dict:
    """Who the email goes to. Nothing from the agency's website: the pitch is an introduction, not a site review."""
    return {
        "name": lead.get("business_name") or "",
        "greeting_name": lead.get("greeting_name") or "",
    }


def generate_agency_email(
    provider: AIProvider, *, lead: dict, site: dict, sender: dict, notes: str, portfolio: dict, timeout: float
) -> dict:
    from app.services.audit_ai import HUMAN_STYLE, _text, parse_json_text

    payload = {
        "agency": agency_context(lead, site),
        "offer": {"notes": notes[:6000]},
        "sender": {
            # no person's name: any inbox may send it, and its own signature carries the name
            "company_name": sender.get("company_name") or "",
            "company_website": sender.get("company_website") or "",
            "signature_added": bool(sender.get("signature_added")),
        },
    }
    text = provider.generate(
        "Write the white-label partnership email to this agency (the company profile PDF is attached).\n"
        "Return JSON only with keys subject, body_html and reference_sites.\n\n"
        + json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False),
        # the shared style, but written as the company ("we"), never one named person
        system=AGENCY_EMAIL_SYSTEM + HUMAN_STYLE + AS_THE_COMPANY,
        temperature=0.4,
        max_tokens=4000,
        json_schema=AGENCY_EMAIL_SCHEMA,
        timeout=timeout,
    )
    data = parse_json_text(text)
    subject = _text(data.get("subject"), 200)
    body = _text(data.get("body_html"), 6000).replace("—", ", ").replace("–", "-")
    if not body:
        raise AIProviderError("The AI email draft was empty.")
    if "<p" not in body.lower():
        body = "".join(f"<p>{line.strip()}</p>" for line in body.split("\n\n") if line.strip())
    if not subject:
        subject = f"white-label help for {lead.get('business_name') or 'your agency'}"[:80]
    links = links_from_notes(notes)
    body = ensure_links(body, links)
    sites = [{"url": str(u)} for u in (data.get("reference_sites") or []) if str(u).strip()]
    return {"subject": subject, "html": body, "reference_sites": sites, "links": links}


FOLLOWUP_SYSTEM = """You write short follow-up emails from a web design and development studio to an agency. The first
email (given) offered to be the agency's white-label delivery partner (the studio does the work, the agency delivers
it under its own brand), with the studio's company profile attached. The agency hasn't replied.

{style}
Rules for the follow-up:
- 30-70 words, 2-3 short paragraphs, plain text (no HTML, no links unless one appears in the first email).
- Start with "Hi <recipient.first_name>," on its own line when a first name is given, otherwise exactly
  "Hi there,". Never greet the agency name.
- Don't repeat the first email. Add one new, small, useful angle: a way to try the partnership with little risk
  (e.g. one small project first), a typical moment agencies need extra hands (a busy month, a client asking for
  something they don't do in-house), or a simple question. Only mention services, prices or numbers that appear in
  the first email. The last follow-up can politely close the loop ("I'll leave it here").
- Never guilt-trip ("just bumping", "did you see my email", "following up on my previous email" are banned).
- No subject line, no P.S., no unsubscribe line, no placeholders.
- End with a short closing like "Best," with nothing after it (the signature is added automatically) when
  signature_added is true; otherwise end with the sender's first name.

Return JSON only: {{"body": "..."}}"""


def fallback_followup(step: int, steps: int, name: str, agency: str, signature_added: bool, sender: str) -> str:
    agency = agency or "your agency"
    if step >= steps:
        text = (
            f"Hi {name},\n\nI'll leave it here so I don't fill your inbox. If {agency} ever needs extra hands on a "
            "client project, under your brand, I'm happy to help.\n\n"
        )
    else:
        text = (
            f"Hi {name},\n\nOne easy way to see if we'd work well together: a single small project for one of "
            f"{agency}'s clients, delivered under your brand.\n\nWould that be useful?\n\n"
        )
    return text + ("Best," if signature_added else f"Best,\n{sender}")

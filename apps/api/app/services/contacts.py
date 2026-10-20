"""Business contacts on a lead's own website, and who to email at that business.

Discovery (during the website audit, or with "Find contacts" on the lead page): the site's public contact, about,
team, leadership and imprint pages are read; every email address on the business's own domain (or a free-mail
address the business publishes, like owner@gmail.com) is kept with the name and job title written next to it, when
stated. Addresses on other domains (web agencies, platforms) are dropped. Nothing is guessed.

Verification is limited to what can honestly be checked: the address was published on the site, and its domain
accepts mail (MX record). Mailbox existence can't be checked from here, so no address is called "verified";
addresses that bounced before, or are on the suppression list, are excluded.

Ranking for the primary recipient (one email per business):
  1. owner / founder / CEO / managing director / president / principal named on the site
  2. a decision-maker for what you offer (marketing, digital, web, general manager)
  3. a relevant department (marketing@, sales@, operations@, partnerships@)
  4. a monitored general inbox (info@, contact@, hello@, enquiries@)
A personal-looking address with no stated role ranks just above a general inbox, never above a named decision-maker.
When there is no individual contact, the best general inbox is kept, so the business is never dropped.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import Lead, LeadEmail, SuppressionEntry
from app.utils.emails import is_plausible_email

logger = get_logger("contacts")

CONTACT_PAGE_HINTS = (
    "about", "team", "our-team", "staff", "people", "leadership", "management", "meet", "who-we-are",
    "our-story", "company", "contact", "get-in-touch", "impressum", "imprint", "legal-notice", "founder", "owner",
)
MAX_EXTRA_PAGES = 6

FREE_MAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.co.uk", "icloud.com",
    "me.com", "aol.com", "msn.com", "bigpond.com", "bigpond.net.au", "optusnet.com.au", "btinternet.com",
    "sky.com", "shaw.ca", "rogers.com", "telus.net", "gmx.de", "web.de", "t-online.de", "protonmail.com",
}
GENERIC = {
    "info", "contact", "hello", "hi", "enquiries", "enquiry", "inquiries", "inquiry", "mail", "office", "admin",
    "team", "general", "reception", "studio", "shop", "store", "email", "home", "welcome",
    # role words (not people), in the languages your leads use: these are shared inboxes, not names
    "reservations", "reservation", "reserve", "appointments", "appointment", "booking", "bookings", "smile", "smiles",
    "care", "patientcare", "patients", "post", "frontdesk", "front", "desk", "clinic", "practice", "surgery", "dental",
    "dentist", "salon", "spa", "events", "event", "catering", "order", "orders", "customercare", "customerservice",
    "service", "services", "quotes", "quote", "estimates", "estimate", "jobs", "hola", "contacto", "reservas",
    "kontakt", "hallo", "buero", "praxis", "empfang", "anfrage", "anfragen", "bestellung", "bonjour", "accueil",
    "office1", "online", "web", "enquire", "ask", "help", "hey", "yo", "staff", "management", "manager", "owner",
    "dispatch", "csr", "estimating", "scheduling", "schedule", "repairs", "installs", "parts", "rentals", "leasing",
}
DEPARTMENT_SCORE = {
    "marketing": 72, "digital": 72, "web": 70, "webmaster": 68, "design": 62, "sales": 66, "partnerships": 64,
    "partners": 64, "business": 62, "operations": 62, "manager": 62, "management": 62, "bookings": 40,
    "booking": 40, "orders": 38, "accounts": 30, "billing": 28, "support": 34, "service": 36, "help": 32,
    "media": 50, "press": 45,
}
# Among general inboxes, the ones the front desk actually reads first; admin@ / mail@ often belong to whoever set up
# the website or hosting and are the likeliest to be dead (equal scores used to be broken alphabetically: admin < info)
GENERIC_SCORE = {
    "info": 53, "contact": 53, "hello": 53, "enquiries": 53, "enquiry": 53, "inquiries": 53, "inquiry": 53,
    "office": 52, "reception": 52, "team": 51, "general": 51, "studio": 51, "shop": 51, "store": 51, "hi": 51,
    "welcome": 50, "mail": 46, "email": 46, "home": 45, "admin": 42,
}
LOCATION_SCORE = 62  # the mailbox for the lead's own branch (e.g. northmiami@ for the North Miami office)
# Never emailed (unless a verification service confirmed the mailbox): the website / hosting admin's inbox
ADMIN_INBOXES = {"admin", "administrator", "webmaster", "hostmaster", "hosting", "it", "tech", "sysadmin", "wordpress",
                 "wp", "web", "dev", "developer"}


def _one_typo_apart(a: str, b: str) -> bool:
    """Two different addresses one letter apart (adventura / aventura): one of them is probably a typo."""
    if a == b or min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _confirmed(row) -> bool:
    return row.verification_status == "valid" and bool((getattr(row, "verification_details", None) or {}).get("provider"))

EXCLUDED_LOCAL = {
    "noreply", "no-reply", "donotreply", "privacy", "legal", "abuse", "postmaster", "unsubscribe", "jobs", "careers",
    "hr", "recruitment", "dmca", "gdpr", "dataprotection", "datenschutz",
}
SENIOR_TITLES = (
    "owner", "co-owner", "founder", "co-founder", "ceo", "chief executive", "managing director", "president",
    "principal", "proprietor", "director", "partner", "geschäftsführer", "inhaber",
)
DECISION_TITLES = ("marketing", "digital", "general manager", "operations manager", "business development", "head of", "manager")
TITLE_RE = re.compile(
    r"\b(co-?founder|founder|co-owner|owner|proprietor|ceo|chief executive officer|chief executive|managing director|"
    r"president|principal|partner|director of [a-z ]{3,30}|[a-z]{3,20} director|director|general manager|"
    r"operations manager|office manager|marketing manager|sales manager|head of [a-z ]{3,30}|[a-z]{3,20} manager|"
    r"manager|coordinator|geschäftsführer|inhaber)\b",
    re.I,
)
NAME_RE = re.compile(r"\b([A-Z][a-z'’-]{1,20}(?:\s+[A-Z][a-z'’-]{1,20}){1,2})\b")
NOT_NAMES = {
    "Contact Us", "About Us", "Our Team", "Meet The", "Get In", "Read More", "Learn More", "Privacy Policy",
    "Terms Of", "Book Now", "Call Us", "Email Us", "Send Message", "Follow Us", "Opening Hours", "Our Story",
}


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower().strip()


def belongs_to_business(email: str, site_domain: str) -> bool:
    """Same domain as the website (or a subdomain), or a free-mail address the business itself publishes."""
    d = domain_of(email)
    site = (site_domain or "").lower().removeprefix("www.")
    return bool(site) and (d == site or d.endswith("." + site) or site.endswith("." + d)) or d in FREE_MAIL


def classify(email: str, name: str = "", title: str = "") -> str:
    """personal | department | generic (from the address, and the name / title found next to it)."""
    local = email.split("@", 1)[0].lower()
    base = re.split(r"[._+-]", local)[0]
    if local in GENERIC or base in GENERIC:
        return "generic"
    if local in DEPARTMENT_SCORE or base in DEPARTMENT_SCORE:
        return "department"
    if name or title:
        return "personal"
    if re.fullmatch(r"[a-z]{2,15}([._][a-z]{1,20})?", local):
        return "personal"  # looks like firstname / first.last (a person, role unknown)
    return "generic"


def score(email: str, email_type: str, title: str = "", name: str = "") -> tuple[int, str]:
    """(score, reason) for choosing the primary recipient (higher is better; 0 = never)."""
    local = email.split("@", 1)[0].lower()
    if local in EXCLUDED_LOCAL or local.split(".")[0] in EXCLUDED_LOCAL:
        return 0, "not an outreach address (no-reply, privacy, jobs...)"
    t = (title or "").lower()
    who = f"{name}, {title}".strip(", ") if (name or title) else email
    if t and any(s in t for s in SENIOR_TITLES) and "assistant" not in t:
        return 100, f"{who}: owner / senior decision-maker named on the website"
    if t and any(s in t for s in DECISION_TITLES):
        return 85, f"{who}: decision-maker for marketing / the website"
    if email_type == "department":
        base = re.split(r"[._+-]", local)[0]
        points = DEPARTMENT_SCORE.get(local, DEPARTMENT_SCORE.get(base, 40))
        return points, f"{local}@ department inbox" + (" relevant to a website offer" if points >= 60 else "")
    if email_type == "personal":
        if t:
            return 58, f"{who}: named contact (role not a decision-maker)"
        if name:
            return 55, f"personal-looking address ({name}), role not stated"
        # Only looks like a name (john@, northmiami@): in your sending these bounced 3x more than info@ and were opened
        # less, so info@ / office@ / hello@ come first when the business has one
        return 50, "personal-looking address, no name or role shown next to it"
    base = re.split(r"[._+-]", local)[0]
    points = GENERIC_SCORE.get(local, GENERIC_SCORE.get(base, 50))
    return points, f"{local}@ general business inbox" + (" (often the website / hosting admin)" if points < 47 else "")


def location_names(lead: Lead) -> dict[str, str]:
    """Squashed place names from the lead's city and address ("northmiami" -> "North Miami"), for branch mailboxes."""
    words = re.findall(r"[A-Za-z]+", f"{getattr(lead, 'city', '') or ''} {getattr(lead, 'address', '') or ''}")
    names = {}
    for size in (1, 2, 3):
        for i in range(len(words) - size + 1):
            phrase = words[i:i + size]
            key = "".join(w.lower() for w in phrase)
            if len(key) >= 5 and not any(ch.isdigit() for ch in key):
                names.setdefault(key, " ".join(phrase))
    return names


# ---------------------------------------------------------------- discovery


def _context(soup: BeautifulSoup, email: str) -> str:
    """Text around where the address appears (its card / list item / paragraph)."""
    node = soup.find("a", href=re.compile(re.escape(email), re.I)) or soup.find(string=re.compile(re.escape(email), re.I))
    if node is None:
        return ""
    block = node if getattr(node, "name", None) else node.parent
    for _ in range(4):
        if block is None or block.parent is None:
            break
        text = block.get_text(" ", strip=True)
        if len(text) > 40:
            break
        block = block.parent
    return (block.get_text(" ", strip=True) if block is not None else "")[:400]


TITLE_WORDS = {
    "founder", "co", "owner", "director", "managing", "manager", "marketing", "sales", "ceo", "president", "principal",
    "partner", "general", "operations", "office", "head", "chief", "executive", "officer", "lead", "senior", "team",
    "contact", "email", "phone", "call", "and",
}


def _clean_name(candidate: str) -> str:
    words = [w for w in candidate.split() if w.lower().strip("'’-") not in TITLE_WORDS]
    return " ".join(words) if len(words) >= 2 else ""


def _name_from(context: str, email: str) -> str:
    local = email.split("@", 1)[0].lower()
    names = [
        n
        for n in (_clean_name(raw) for raw in NAME_RE.findall(context) if raw not in NOT_NAMES)
        if n and not n.lower().startswith(("our ", "the "))
    ]
    first = re.split(r"[._-]", local)[0]
    for n in names:  # prefer a name that matches the address (john@ / john.smith@)
        if first and first in n.lower():
            return n
    return names[0] if len(names) == 1 else ""


def _title_from(context: str) -> str:
    match = TITLE_RE.search(context or "")
    return match.group(1).strip().title() if match else ""


def _fetch(client: httpx.Client, url: str) -> tuple[str, str]:
    try:
        response = client.get(url)
        if response.status_code < 400 and "html" in response.headers.get("content-type", "html"):
            return str(response.url), response.text
    except Exception as exc:
        logger.info("contact_page_failed", url=url, error=str(exc)[:120])
    return url, ""


def _contact_pages(home_url: str, home_html: str, site_domain: str, skip: set[str]) -> list[str]:
    soup = BeautifulSoup(home_html or "", "lxml")
    found: list[tuple[int, str]] = []
    seen = set(skip)
    for a in soup.find_all("a", href=True):
        href = urljoin(home_url, a["href"])
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or not parsed.netloc.lower().endswith(site_domain):
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if clean in seen:
            continue
        hay = f"{parsed.path} {a.get_text(' ', strip=True)}".lower()
        points = sum(1 for hint in CONTACT_PAGE_HINTS if hint in hay)
        if points:
            seen.add(clean)
            found.append((points, clean))
    found.sort(reverse=True)
    return [u for _, u in found[:MAX_EXTRA_PAGES]]


def extract_contacts(pages: list[tuple[str, str]], site_domain: str) -> list[dict]:
    """[{email, contact_name, job_title, email_type, source_url}] from (url, html) pages; business addresses only."""
    from app.services.email_extraction import extract_emails_from_html

    contacts: dict[str, dict] = {}
    for url, html in pages:
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for item in extract_emails_from_html(html, url, site_domain):
            email = item["email"].lower().strip()
            if not is_plausible_email(email) or not belongs_to_business(email, site_domain):
                continue  # third-party addresses (agency, platform, plugin) are not this business
            context = _context(soup, email)
            name, title = _name_from(context, email), _title_from(context)
            entry = contacts.setdefault(email, {"email": email, "contact_name": "", "job_title": "", "source_url": url})
            entry["contact_name"] = entry["contact_name"] or name
            entry["job_title"] = entry["job_title"] or title
    for entry in contacts.values():
        entry["email_type"] = classify(entry["email"], entry["contact_name"], entry["job_title"])
    return list(contacts.values())


def discover(db: Session, lead: Lead, pages: list[tuple[str, str]] | None = None) -> list[LeadEmail]:
    """Read the business's own pages for contacts, store them on the lead, and re-pick its primary recipient."""
    from app.utils.normalize import extract_domain, normalize_url

    if not lead.website_url:
        return list(lead.emails)
    home = normalize_url(lead.website_url)
    site_domain = (lead.website_domain or extract_domain(home) or "").lower().removeprefix("www.")
    pages = list(pages or [])
    try:
        with httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            if not pages:
                pages.append(_fetch(client, home))
            have = {u.rstrip("/") for u, _ in pages}
            home_url, home_html = pages[0]
            for url in _contact_pages(home_url, home_html, site_domain, have):
                pages.append(_fetch(client, url))
    except Exception as exc:
        logger.warning("contact_discovery_failed", lead_id=lead.id, error=str(exc)[:200])
    found = extract_contacts(pages, site_domain)
    now = datetime.now(UTC)
    existing = {e.email.lower(): e for e in lead.emails}
    for c in found:
        row = existing.get(c["email"])
        if row is None:
            row = LeadEmail(lead_id=lead.id, email=c["email"], source_url=c["source_url"])
            db.add(row)
            lead.emails.append(row)
            existing[c["email"]] = row
        row.contact_name = row.contact_name or c["contact_name"]
        row.job_title = row.job_title or c["job_title"]
        row.email_type = c["email_type"]
        row.discovered_at = now
    db.flush()
    refresh_verification(db, lead)
    choose_primary(db, lead)
    db.commit()
    logger.info("contacts_discovered", lead_id=lead.id, found=len(found), total=len(lead.emails))
    return list(lead.emails)


def refresh_verification(db: Session, lead: Lead) -> None:
    """published / domain_ok / domain_rejects / bounced / suppressed (never "verified": mailboxes can't be checked)."""
    from app.services.deliverability import domain_accepts_mail

    suppressed = {
        s.lower()
        for (s,) in db.query(SuppressionEntry.email).filter(
            SuppressionEntry.user_id == lead.user_id, SuppressionEntry.email.in_([e.email.lower() for e in lead.emails])
        )
    }
    for row in lead.emails:
        address = row.email.lower()
        if address in suppressed:
            row.verification_status = "suppressed"
            continue
        if row.verification_status in ("bounced", "suppressed"):
            continue
        if row.verification_status in PROVIDER_STATUSES and (getattr(row, "verification_details", None) or {}).get("provider"):
            continue  # a verification service's answer is better than the domain check: keep it
        accepts = domain_accepts_mail(address)
        row.verification_status = "domain_ok" if accepts else "domain_rejects" if accepts is False else "published"
        row.is_verified = False  # only bounces / replies can prove a mailbox; never claimed here


# ---------------------------------------------------------------- choosing recipients

ELIGIBLE = {"domain_ok", "published", "not_checked", "valid", "accept_all", "unknown"}
PROVIDER_STATUSES = {"valid", "invalid", "accept_all", "unknown"}  # set by a verification service: kept as they are


def ranked_contacts(lead: Lead, include_generic: bool = True) -> list[tuple[LeadEmail, int, str]]:
    rows = []
    places = location_names(lead)
    locals_by_domain: dict[str, list[str]] = {}
    for row in lead.emails:
        local_part, _, dom = row.email.lower().partition("@")
        locals_by_domain.setdefault(dom, []).append(re.sub(r"[^a-z]", "", local_part))
    for row in lead.emails:
        if row.verification_status not in ELIGIBLE:
            continue
        local_part, _, dom = row.email.lower().partition("@")
        squashed = re.sub(r"[^a-z]", "", local_part)
        from app.utils.emails import is_plausible_email

        if not is_plausible_email(row.email):
            continue  # an image file name, a template placeholder, a website vendor's address
        if not _confirmed(row):
            if local_part in ADMIN_INBOXES or re.split(r"[._+-]", local_part)[0] in ADMIN_INBOXES:
                continue  # the website / hosting admin's inbox: often dead, never the right person
            if squashed not in places and any(_one_typo_apart(squashed, o) for o in locals_by_domain.get(dom, [])):
                continue  # one of two near-identical addresses (a likely typo on their site): use another one
        kind = row.email_type if row.email_type in ("personal", "department", "generic") else classify(
            row.email, row.contact_name, row.job_title
        )
        if kind == "generic" and not include_generic:
            continue
        points, reason = score(row.email, kind, row.job_title, row.contact_name)
        local = re.sub(r"[^a-z]", "", row.email.split("@", 1)[0].lower())
        if points > 0 and local in places and points < LOCATION_SCORE:
            points, reason = LOCATION_SCORE, f"{row.email.split('@', 1)[0]}@ is the mailbox for this location ({places[local]})"
        if points > 0 and not _confirmed(row):
            from app.services.email_verification import must_confirm

            other = must_confirm(row.email, getattr(lead, "website_url", "") or "")
            if other:
                # another company's domain (the parent company, a neighbouring practice, the web agency): the
                # business's own addresses come first; it's only used once a verification service confirms it
                points, reason = max(points - 20, 1), other
        if points > 0:
            rows.append((row, points, reason))
    return sorted(rows, key=lambda r: (-r[1], r[0].email))


def address_risk(lead: Lead, email: str) -> str:
    """Why this address type has a real chance of bouncing ("" = a safe type). Safe types: a general inbox on the
    business's own domain (info@, office@...), a department inbox, a person named on the website, the mailbox for the
    lead's own location, Gmail / Outlook / Yahoo. Measured on real sending: another company's domain bounced 17%,
    addresses that only look like a name 9%, admin@ 33%; info@ 3%, webmail 0%."""
    from app.services.email_verification import LEGACY_ISP, WEBMAIL, must_confirm

    address = (email or "").strip().lower()
    local, _, domain = address.partition("@")
    other = must_confirm(address, getattr(lead, "website_url", "") or "")
    if other:
        return f"{other} (these bounced 17% in your sending)"
    if domain in WEBMAIL:
        return ""
    if domain in LEGACY_ISP:
        return f"{domain} is an old internet-provider address (many were closed or moved)"
    base = re.split(r"[._+-]", local)[0]
    if local in ADMIN_INBOXES or base in ADMIN_INBOXES:
        return f"{local}@ is usually the website / hosting admin's inbox (these bounced 33% in your sending)"
    row = next((r for r in getattr(lead, "emails", None) or [] if r.email.lower() == address), None)
    name, title = (row.contact_name, row.job_title) if row is not None else ("", "")
    kind = (row.email_type if row is not None and row.email_type in ("personal", "department", "generic")
            else classify(address, name, title))
    if kind == "personal" and not (name or title):
        if re.sub(r"[^a-z]", "", local) in location_names(lead):
            return ""  # the mailbox for this lead's own branch
        return (f"{local}@ only looks like a person's name, with no name or role shown on the site (these bounced 9% "
                "in your sending)")
    return ""


def reject_and_fall_back(db: Session, lead: Lead, email: str, status: str = "invalid") -> str | None:
    """This address won't do (it would bounce): mark it, and choose the business's next-best address.
    Returns the new primary address, or None when there is no other."""
    address = (email or "").strip().lower()
    for row in lead.emails:
        if row.email.lower() == address:
            row.verification_status = status
    chosen = choose_primary(db, lead)
    db.commit()
    return lead.primary_email if chosen is not None and lead.primary_email.lower() != address else None


def choose_primary(db: Session, lead: Lead) -> LeadEmail | None:
    """Set the lead's primary recipient: your manual choice when there is one, else the best-ranked contact."""
    override = ((lead.extra or {}).get("primary_override") or "").lower()
    ranked = ranked_contacts(lead)
    for row, points, reason in ranked:
        row.rank_reason = reason
    chosen = next((r for r, _, _ in ranked if r.email.lower() == override), None) if override else None
    if chosen is None and ranked:
        chosen = ranked[0][0]
    for row in lead.emails:
        row.is_primary = row is chosen
    if chosen is not None:
        if override and chosen.email.lower() == override:
            chosen.rank_reason = "chosen by you"
        lead.primary_email = chosen.email
    return chosen


def select_recipients(
    db: Session, lead: Lead, *, include_generic: bool = True, cc_enabled: bool = False, cc_max: int = 2
) -> dict:
    """{"to", "reason", "cc"} for one business. CC only relevant named / department contacts of the same business,
    never the primary, never suppressed or ineligible addresses, never general inboxes, at most cc_max."""
    from app.services.suppression import is_suppressed

    if lead.emails:
        refresh_needed = [r for r in lead.emails if r.verification_status in ("", "not_checked")]
        if refresh_needed:
            refresh_verification(db, lead)
    override = ((lead.extra or {}).get("primary_override") or "").lower()
    ranked = [r for r in ranked_contacts(lead, include_generic) if not is_suppressed(db, lead.user_id, r[0].email)]
    primary = next((r for r in ranked if r[0].email.lower() == override), None) or (ranked[0] if ranked else None)
    if primary is None:
        fallback = (lead.primary_email or "").lower()
        if fallback and (include_generic or classify(fallback) != "generic") and not is_suppressed(db, lead.user_id, fallback):
            return {"to": fallback, "reason": "the lead's email", "cc": []}
        return {"to": "", "reason": "no eligible address" + ("" if include_generic else " (general inboxes excluded)"), "cc": []}
    to = primary[0].email.lower()
    cc = []
    if cc_enabled:
        for row, points, reason in ranked:
            kind = row.email_type if row.email_type in ("personal", "department") else classify(row.email, row.contact_name, row.job_title)
            if row.email.lower() == to or kind == "generic" or points < 60:
                continue
            if domain_of(row.email) != domain_of(to) and domain_of(row.email) in FREE_MAIL:
                continue  # a private-looking free-mail address is not CC'd alongside the business address
            cc.append(row.email.lower())
            if len(cc) >= max(0, cc_max):
                break
    return {"to": to, "reason": "chosen by you" if override and to == override else primary[2], "cc": cc}


def contacts_payload(db: Session, lead: Lead) -> dict:
    """Everything the lead page shows: company, every address with its type / status / reason, the choice."""
    choice = select_recipients(db, lead, cc_enabled=True)
    ranked = {r.email.lower(): (points, reason) for r, points, reason in ranked_contacts(lead)}
    rows = []
    for row in sorted(lead.emails, key=lambda r: -ranked.get(r.email.lower(), (0, ""))[0]):
        kind = row.email_type if row.email_type in ("personal", "department", "generic") else classify(
            row.email, row.contact_name, row.job_title
        )
        points, reason = ranked.get(row.email.lower(), (0, "not eligible"))
        rows.append(
            {
                "id": row.id,
                "email": row.email,
                "contact_name": row.contact_name,
                "job_title": row.job_title,
                "type": {"personal": "Personal business contact", "department": "Department inbox"}.get(kind, "Generic inbox"),
                "type_key": kind,
                "verification": row.verification_status,
                "source_url": row.source_url,
                "score": points,
                "reason": reason,
                "primary": row.email.lower() == choice["to"],
                "suggested_cc": row.email.lower() in choice["cc"],
            }
        )
    return {
        "company": lead.business_name,
        "domain": (lead.website_domain or "").removeprefix("www."),
        "primary": choice["to"],
        "primary_reason": choice["reason"],
        "suggested_cc": choice["cc"],
        "manual_override": bool((lead.extra or {}).get("primary_override")),
        "contacts": rows,
    }


def set_primary_override(db: Session, lead: Lead, email: str | None) -> None:
    """Your choice of primary recipient for this business ("" / None: back to the automatic choice)."""
    extra = dict(lead.extra or {})
    if email:
        if email.lower() not in {e.email.lower() for e in lead.emails}:
            raise ValueError("That address isn't one of this business's contacts.")
        extra["primary_override"] = email.lower()
    else:
        extra.pop("primary_override", None)
    lead.extra = extra
    choose_primary(db, lead)
    db.commit()


# ---------------------------------------------------------------- campaign / file options and de-duplication

DEFAULT_OPTIONS = {"include_generic": True, "cc_enabled": False, "cc_max": 2}


def recipient_options(extra: dict | None) -> dict:
    """A campaign's / scrape file's recipient options (extra["recipients"]), with defaults."""
    return {**DEFAULT_OPTIONS, **((extra or {}).get("recipients") or {})}


def company_key(lead: Lead) -> str:
    """The business's domain: one company, however many addresses it has (free-mail domains aren't companies)."""
    domain = (lead.website_domain or "").lower().removeprefix("www.")
    return "" if not domain or domain in FREE_MAIL else domain


def company_already_contacted(db: Session, lead: Lead, campaign_id: str | None, any_campaign: bool) -> str:
    """Why this company must not get another first email ("" = it may): one campaign message per company.

    Checks first emails (not follow-ups / replies) sent or being sent to this lead or to any lead on the same
    business domain: in this campaign always, and in any campaign when previously contacted companies are skipped.
    """
    from app.models import EmailMessage

    domain = company_key(lead)
    lead_ids = [lead.id]
    if domain:
        lead_ids = [
            row[0]
            for row in db.query(Lead.id).filter(Lead.user_id == lead.user_id, Lead.website_domain.in_([domain, f"www.{domain}"]))
        ] or [lead.id]
    query = db.query(EmailMessage).filter(
        EmailMessage.user_id == lead.user_id,
        EmailMessage.lead_id.in_(lead_ids),
        EmailMessage.status.in_(["sent", "sending"]),
        EmailMessage.followup_of_id.is_(None),
        EmailMessage.is_test.is_(False),
        EmailMessage.subject.notilike("re:%"),  # replies in a conversation are not new outreach
    )
    if not any_campaign:
        if not campaign_id:
            return ""
        query = query.filter(EmailMessage.campaign_id == campaign_id)
    previous = query.order_by(EmailMessage.sent_at.desc()).first()
    if not previous:
        return ""
    where = "in this campaign" if campaign_id and previous.campaign_id == campaign_id else "before"
    return f"{lead.business_name or domain} was already emailed {where} (to {previous.recipient_email})."

"""Optional mailbox verification before outreach, with a provider's free monthly allowance.

Why a provider: from a home connection a mail server can't be asked whether a mailbox exists (Google answers "OK"
to every address and bounces later; Microsoft 365 doesn't answer at all). Verification services check from trusted
servers and keep data on dead mailboxes, so they catch closed inboxes the free checks can't.

How it's used, to stay within a small free allowance:
  - only right before an audit starts (or a campaign email is sent), so a dead address costs no Claude tokens
  - scope "risky" (default): only leads more likely to bounce: an address that isn't on the website's own domain,
    or a business type that has bounced 5% or more in your own sending
  - each address is checked once (the result is stored on the lead's email)
  - at most `email_verifier_monthly_limit` checks a month; when the allowance runs out, outreach carries on as
    before (the free checks still apply), never stops

Results: valid / invalid / accept_all (the domain accepts every address, so it can't be confirmed) / unknown.
Only "invalid" stops the email; anything else goes out as before.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.logging_config import get_logger
from app.models import EmailMessage, Lead, LeadEmail, UserSettings
from app.utils.crypto import decrypt_text

logger = get_logger("email_verify")

PROVIDERS = {"zerobounce": "ZeroBounce", "hunter": "Hunter", "own": "Your mailbox checker"}
CHECKED = {"valid", "invalid", "accept_all", "unknown"}
RISKY_BOUNCE_RATE, RISKY_MIN_SENT = 0.03, 10  # 3%: steady, not flipping around 5% from one email to the next
DELIVERABLE_DO_NOT_MAIL = {"role_based": "valid", "role_based_catch_all": "accept_all", "mx_forward": "valid"}
ADMIN_LIKE = {"admin", "administrator", "mail", "email", "webmaster", "web", "hosting", "it", "tech"}
TIMEOUT = 20


class VerifierError(Exception):
    def __init__(self, message: str, quota: bool = False) -> None:
        super().__init__(message)
        self.quota = quota


# ---------------------------------------------------------------- providers


def verify_with(provider: str, key: str, email: str, url: str = "") -> dict:
    """{"status": valid | invalid | accept_all | unknown, "detail": str, "raw": {...}} from the provider."""
    if provider == "own":
        if not url:
            raise VerifierError("Add your checker's address (e.g. https://verify.example.com).")
        response = httpx.post(f"{url.rstrip('/')}/verify", json={"email": email},
                              headers={"Authorization": f"Bearer {key}"}, timeout=60)
        if response.status_code == 401:
            raise VerifierError("Your checker didn't accept the token.")
        if response.status_code >= 400:
            raise VerifierError(f"Your checker answered HTTP {response.status_code}.")
        data = response.json()
        status = data.get("status") if data.get("status") in CHECKED else "unknown"
        return {"status": status, "detail": str(data.get("detail") or "")[:200], "raw": data}
    if provider == "zerobounce":
        response = httpx.get(
            "https://api.zerobounce.net/v2/validate",
            params={"api_key": key, "email": email, "ip_address": ""},
            timeout=TIMEOUT,
        )
        data = response.json() if response.content else {}
        if response.status_code >= 400 or data.get("error"):
            message = str(data.get("error") or f"HTTP {response.status_code}")
            raise VerifierError(f"ZeroBounce: {message}", quota="credit" in message.lower() or response.status_code in (402, 429))
        status, sub = str(data.get("status") or "").lower(), str(data.get("sub_status") or "").lower()
        mapped = {
            "valid": "valid", "invalid": "invalid", "spamtrap": "invalid", "abuse": "invalid", "do_not_mail": "invalid",
            "catch-all": "accept_all",
        }.get(status, "unknown")
        if status == "do_not_mail" and sub in DELIVERABLE_DO_NOT_MAIL:
            # ZeroBounce's "do not mail" for marketing lists also covers shared inboxes (info@, office@): they exist,
            # and for a small business they're usually the right address
            mapped = DELIVERABLE_DO_NOT_MAIL[sub]
        return {"status": mapped, "detail": " / ".join(x for x in (status, data.get("sub_status")) if x), "raw": data}
    if provider == "hunter":
        response = httpx.get(
            "https://api.hunter.io/v2/email-verifier", params={"email": email, "api_key": key}, timeout=TIMEOUT
        )
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            errors = data.get("errors") or [{}]
            message = str(errors[0].get("details") or f"HTTP {response.status_code}")
            raise VerifierError(f"Hunter: {message}", quota=response.status_code in (402, 429) or "limit" in message.lower())
        info = data.get("data") or {}
        status, result = str(info.get("status") or "").lower(), str(info.get("result") or "").lower()
        if status in ("invalid", "disposable") or result == "undeliverable":
            mapped = "invalid"
        elif status == "valid" or result == "deliverable":
            mapped = "valid"
        elif status == "accept_all":
            mapped = "accept_all"
        else:
            mapped = "unknown"
        return {"status": mapped, "detail": " / ".join(x for x in (status, result) if x), "raw": info}
    raise VerifierError(f"Unknown verification provider: {provider}")


def account_status(provider: str, key: str, url: str = "") -> str:
    """Check the key without spending a check: what the provider says is left."""
    if provider == "own":
        if not url:
            raise VerifierError("Add your checker's address first.")
        response = httpx.get(f"{url.rstrip('/')}/status", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if response.status_code == 401:
            raise VerifierError("Your checker didn't accept the token.")
        if response.status_code >= 400:
            raise VerifierError(f"Your checker answered HTTP {response.status_code}.")
        data = response.json()
        if not data.get("port25"):
            raise VerifierError("Your checker is up, but its server can't reach mail servers on port 25. Ask the "
                                "hosting company to open outbound port 25.")
        return f"Your checker works (checking as {data.get('mail_from')}, port 25 open)."
    if provider == "zerobounce":
        response = httpx.get("https://api.zerobounce.net/v2/getcredits", params={"api_key": key}, timeout=TIMEOUT)
        credits = str((response.json() if response.content else {}).get("Credits", ""))
        if response.status_code >= 400 or credits in ("", "-1"):
            raise VerifierError("ZeroBounce didn't accept this API key.")
        return f"Key works. {credits} ZeroBounce credits left."
    if provider == "hunter":
        response = httpx.get("https://api.hunter.io/v2/account", params={"api_key": key}, timeout=TIMEOUT)
        if response.status_code >= 400:
            raise VerifierError("Hunter didn't accept this API key.")
        verifications = (((response.json() or {}).get("data") or {}).get("requests") or {}).get("verifications") or {}
        return f"Key works. Hunter verifications this month: {verifications.get('used', '?')} used of {verifications.get('available', '?')}."
    raise VerifierError("Choose a verification provider first.")


# ---------------------------------------------------------------- which leads are worth a check


def site_domain(website: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", (website or "").strip().lower()).split("/")[0].split(":")[0]


def risky_categories(db: Session, user_id: str) -> dict[str, float]:
    """Business types that have bounced RISKY_BOUNCE_RATE or more in your own sending (lower-case -> rate)."""
    rows = (
        db.query(
            func.lower(Lead.category),
            func.count(EmailMessage.id),
            func.count(EmailMessage.bounced_at),
        )
        .join(Lead, Lead.id == EmailMessage.lead_id)
        .filter(
            EmailMessage.user_id == user_id,
            EmailMessage.sent_at.isnot(None),
            EmailMessage.is_test.is_(False),
            EmailMessage.followup_of_id.is_(None),
        )
        .group_by(func.lower(Lead.category))
        .all()
    )
    return {c: b / s for c, s, b in rows if c and s >= RISKY_MIN_SENT and b / s >= RISKY_BOUNCE_RATE}


WEBMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "yahoo.co.uk",
    "icloud.com", "me.com", "hotmail.co.uk", "outlook.co.uk", "ymail.com", "protonmail.com", "proton.me", "gmx.de",
    "web.de", "gmx.com", "zoho.com",
}
# Old internet-provider addresses (many accounts were closed or moved): checked when possible
LEGACY_ISP = {
    "verizon.net", "aol.com", "att.net", "sbcglobal.net", "bellsouth.net", "comcast.net", "cox.net", "charter.net",
    "earthlink.net", "juno.com", "netzero.net", "netzero.com", "rr.com", "roadrunner.com", "frontier.com",
    "windstream.net", "centurylink.net", "embarqmail.com", "mindspring.com", "prodigy.net", "optonline.net",
    "btinternet.com", "talktalk.net", "ntlworld.com", "virginmedia.com", "blueyonder.co.uk", "shaw.ca", "sympatico.ca",
    "bigpond.com", "optusnet.com.au",
}


def must_confirm(email: str, website: str) -> str:
    """Addresses that are only used when a verification service confirms them (else skipped): another company's
    domain (in your sending they bounced 17%: templates, the web agency, the parent company...). Gmail / Outlook /
    Yahoo addresses the business publishes are fine (0% bounces)."""
    domain, site = (email or "").rsplit("@", 1)[-1].lower(), site_domain(website)
    if not site or domain in WEBMAIL or domain in LEGACY_ISP:
        return ""
    if domain == site or domain.endswith("." + site) or site.endswith("." + domain):
        return ""
    if domain.split(".")[0] == site.split(".")[0]:
        return ""  # the same name on another ending (.com / .net): the business's own second domain
    return f"{email} is on a different company's domain than {site}"


def risky_reason(email: str, website: str, category: str, risky: dict[str, float]) -> str:
    """Why this lead is more likely to bounce ("" = not especially)."""
    domain = (email or "").rsplit("@", 1)[-1].lower()
    other = must_confirm(email, website)
    if other:
        return other
    if domain in WEBMAIL:
        return ""  # never bounced in your sending: no check spent on it
    if domain in LEGACY_ISP:
        return f"{domain} is an old internet-provider address (many were closed or moved)"
    local = (email or "").split("@", 1)[0].lower()
    if local in ADMIN_LIKE or re.split(r"[._+-]", local)[0] in ADMIN_LIKE:
        return f"{local}@ is often the website / hosting admin's inbox, and often dead"
    rate = risky.get((category or "").lower())
    if rate is not None:
        return f"{category} leads have bounced {rate:.0%} in your sending"
    return ""


# ---------------------------------------------------------------- the check before outreach


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _usage(settings: UserSettings) -> dict:
    usage = dict(settings.email_verifier_usage or {})
    if usage.get("month") != _month():
        usage = {"month": _month(), "used": 0, "exhausted": False}
    return usage


def _save_usage(db: Session, settings: UserSettings, usage: dict) -> None:
    settings.email_verifier_usage = usage
    flag_modified(settings, "email_verifier_usage")


def verification_problem(db: Session, user_id: str, lead: Lead | None, email: str) -> str:
    """Before an audit / campaign email: "" to go ahead, else why not (the verifier says the mailbox doesn't
    exist). Never blocks for any other reason: no provider, not risky, allowance used up, or an error."""
    if not lead or not email:
        return ""
    must = must_confirm(email, lead.website_url or "")
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not settings or settings.email_verifier_provider not in PROVIDERS or not settings.encrypted_email_verifier_key:
        return _unconfirmed(must, "no verification service is set up")
    address = email.strip().lower()
    stored = (
        db.query(LeadEmail)
        .filter(LeadEmail.lead_id == lead.id, func.lower(LeadEmail.email) == address)
        .first()
    )
    if stored and stored.verification_status in CHECKED:
        status = stored.verification_status
        if must and status == "unknown":
            return _unconfirmed(must, "the verification service couldn't tell")
        return _problem(status, address, (stored.verification_details or {}).get("detail", ""), settings)
    reason = ""
    if settings.email_verifier_scope != "all":
        reason = risky_reason(address, lead.website_url or "", lead.category or "", risky_categories(db, user_id))
        if not reason:
            return ""
    usage = _usage(settings)
    own = settings.email_verifier_provider == "own"  # your own checker: no allowance to stay within
    if not own and (usage.get("exhausted") or usage.get("used", 0) >= (settings.email_verifier_monthly_limit or 0)):
        return _unconfirmed(must, "this month's verification checks are used up")  # others carry on as before
    try:
        key = decrypt_text(settings.encrypted_email_verifier_key)
        result = verify_with(settings.email_verifier_provider, key, address, settings.email_verifier_url or "")
    except VerifierError as exc:
        logger.warning("email_verify_failed", provider=settings.email_verifier_provider, error=str(exc)[:200])
        if exc.quota:
            usage["exhausted"] = True
            _save_usage(db, settings, usage)
            db.commit()
        return _unconfirmed(must, "the verification service didn't answer")
    except Exception as exc:  # network trouble: never block an ordinary address on it
        logger.warning("email_verify_error", provider=settings.email_verifier_provider, error=str(exc)[:200])
        return _unconfirmed(must, "the verification service didn't answer")
    usage["used"] = usage.get("used", 0) + 1
    _save_usage(db, settings, usage)
    details = {
        "provider": settings.email_verifier_provider,
        "detail": result["detail"],
        "why_checked": reason or "every lead is checked",
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if stored is None:
        stored = LeadEmail(lead_id=lead.id, email=address, is_primary=address == (lead.primary_email or "").lower())
        db.add(stored)
    stored.verification_status, stored.verification_details = result["status"], details
    stored.is_verified = result["status"] == "valid"
    if address == (lead.primary_email or "").strip().lower():
        lead.email_verification_status = result["status"]
    db.commit()
    logger.info("email_verified", email=address, status=result["status"], why=reason or "all")
    if must and result["status"] == "unknown":
        return _unconfirmed(must, "the verification service couldn't tell")
    return _problem(result["status"], address, result["detail"], settings)


def _unconfirmed(must: str, why: str) -> str:
    """An address that is only used once confirmed, and couldn't be ("" for every other address)."""
    if not must:
        return ""
    return f"{must} (these bounced 17% in your sending) and couldn't be confirmed ({why}), so it's skipped."


def _problem(status: str, email: str, detail: str, settings: UserSettings) -> str:
    if status != "invalid":
        return ""
    name = PROVIDERS.get(settings.email_verifier_provider, "The verifier")
    return f"{name} says the mailbox {email} doesn't exist ({detail or 'invalid'}), so it would bounce."


def usage_summary(settings: UserSettings | None) -> dict:
    if not settings:
        return {"month": _month(), "used": 0, "limit": 0, "exhausted": False}
    usage = _usage(settings)
    return {**usage, "limit": settings.email_verifier_monthly_limit}

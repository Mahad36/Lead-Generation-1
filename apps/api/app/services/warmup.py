"""Warm-up between your own inboxes, plus the inbox health checks (spam placement test, domain check).

Warm-up (needs 2+ connected Gmail/SMTP inboxes with warm-up on): during your sending hours each inbox sends a few
natural-looking emails a day to your other inboxes (2 a day at first, rising to 8). The receiving inbox, over IMAP:
takes it out of Spam if it landed there, marks it read and important, files it under a "Warm-up" label out of the
inbox, and answers about a third of them in the same thread. Those positive signals build each inbox's reputation.
It is a light boost: a real warm-up network uses thousands of strangers' inboxes, and real replies from leads count most.
Warm-up emails are kept apart from outreach (own table): they never touch daily limits, stats or conversations.
"""

from __future__ import annotations

import email
import imaplib
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage, UserSettings, WarmupMessage, WebsiteAudit
from app.services.deliverability import dns_query, in_send_window
from app.services.gmail import EmailAttachment, GmailSendError, _smtp_payload, send_gmail_message
from app.utils.dates import local_day_start

logger = get_logger("warmup")

WARMUP_LABEL = "Warm-up"
REPLY_CHANCE = 0.35
HANDLE_AFTER = timedelta(minutes=2)  # give Gmail time to deliver before looking

SUBJECTS = [
    "Quick question about Thursday",
    "Notes from today",
    "Re: the brief",
    "Photos for the homepage",
    "Invoice timing",
    "Can we move our call?",
    "Feedback on the draft",
    "Thanks for the intro",
    "Lunch next week?",
    "Updated timeline",
    "That article you mentioned",
    "Small change to the logo",
    "Following our chat",
    "Plan for Monday",
    "Copy for the about page",
]
OPENERS = [
    "Hope your week is going well.",
    "Thanks again for the quick turnaround earlier.",
    "Hope the move went smoothly.",
    "Good to catch up yesterday.",
    "Hope the weekend was a restful one.",
]
MIDDLES = [
    "I had a look at the draft and the layout works well. The only thing I'd change is the spacing under the header.",
    "Could we push our call to Thursday afternoon? Something came up on my side on Wednesday.",
    "I've attached nothing yet, but I'll send the photos over once the client approves them.",
    "The new copy reads much better. I trimmed the second paragraph a little so it fits on mobile.",
    "The invoice can go out at the end of the month, whichever suits you best.",
    "I spoke to the printer and they can do the run next week if we get the files to them by Friday.",
    "I found the article I mentioned, it's the one about small shops moving online. Worth a read when you have time.",
    "We're on track for the launch. I'll test the contact form on my phone tonight.",
    "The logo works well in black, less so in yellow on white. Shall we keep the dark version for the site?",
    "Happy to meet for lunch next week. Tuesday or Wednesday both work for me.",
]
CLOSERS = ["Speak soon,", "Thanks,", "Cheers,", "Best,", "Talk later,"]
REPLIES = [
    "Thanks, that works for me.",
    "Sounds good, let's do that.",
    "Perfect, thanks for letting me know.",
    "Great, I'll take a look this afternoon.",
    "Thursday is fine. Speak then.",
    "Makes sense. I'll update it on my side.",
    "Thanks for sending this over, much appreciated.",
]


# ---------------------------------------------------------------- IMAP helpers


@contextmanager
def _imap(account: EmailAccount):
    from app.services.inbox import imap_host_for

    data = _smtp_payload(account) or {}
    host = imap_host_for(data)
    if not host:
        raise RuntimeError("No IMAP server for this account")
    imap = imaplib.IMAP4_SSL(host, int(data.get("imap_port") or 993), timeout=30)
    try:
        imap.login(data.get("username") or account.email_address, data.get("password") or "")
        yield imap
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _special_folder(imap: imaplib.IMAP4, flag: str, fallback: str) -> str:
    """A Gmail special folder by its flag (\\All, \\Junk, \\Trash), whatever its local name."""
    typ, folders = imap.list()
    for raw in (folders or []) if typ == "OK" else []:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if flag in line:
            match = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
            if match:
                return match.group(1)
    return fallback


def _find(imap: imaplib.IMAP4, folder: str, rfc_message_id: str) -> list[bytes]:
    if imap.select(f'"{folder}"')[0] != "OK":
        return []
    typ, found = imap.uid("SEARCH", None, "HEADER", "Message-ID", rfc_message_id)
    return (found[0].split() if typ == "OK" and found and found[0] else [])


def locate(imap: imaplib.IMAP4, rfc_message_id: str) -> tuple[str, str, list[bytes]]:
    """Where a delivered email landed: ("inbox" | "promotions" | "spam" | "missing", folder, uids)."""
    spam = _special_folder(imap, "\\Junk", "[Gmail]/Spam")
    uids = _find(imap, spam, rfc_message_id)
    if uids:
        return "spam", spam, uids
    all_mail = _special_folder(imap, "\\All", "[Gmail]/All Mail")
    uids = _find(imap, all_mail, rfc_message_id)
    if not uids:
        return "missing", "", []
    try:
        typ, promo = imap.uid("SEARCH", None, "X-GM-RAW", f'"rfc822msgid:{rfc_message_id.strip("<>")} category:promotions"')
        if typ == "OK" and promo and promo[0]:
            return "promotions", all_mail, uids
    except imaplib.IMAP4.error:
        pass  # not Gmail: no categories
    return "inbox", all_mail, uids


def _move(imap: imaplib.IMAP4, uids: list[bytes], target: str) -> None:
    joined = b",".join(uids).decode()
    try:
        if imap.uid("MOVE", joined, target)[0] == "OK":
            return
    except imaplib.IMAP4.error:
        pass
    imap.uid("COPY", joined, target)
    imap.uid("STORE", joined, "+FLAGS", "(\\Deleted)")
    imap.expunge()


# ---------------------------------------------------------------- warm-up


def _settings(db: Session, user_id: str) -> UserSettings | None:
    return db.query(UserSettings).filter(UserSettings.user_id == user_id).first()


def warmup_accounts(db: Session, user_id: str) -> list[EmailAccount]:
    """Inboxes taking part: connected SMTP (IMAP) inboxes with warm-up on and not paused."""
    return [
        a
        for a in db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.status == "connected")
        if a.warmup_enabled and not a.paused_reason and _smtp_payload(a)
    ]


def daily_target(account: EmailAccount) -> int:
    """2 a day at first, one more every other day, up to 8."""
    created = account.created_at or datetime.now(UTC)
    days = max((datetime.now(UTC) - created).days, 0)
    return min(2 + days // 2, 8)


def _sent_today(db: Session, account: EmailAccount) -> int:
    return (
        db.query(func.count(WarmupMessage.id))
        .filter(
            WarmupMessage.from_account_id == account.id,
            WarmupMessage.sent_at >= local_day_start(),
            WarmupMessage.in_reply_to == "",
        )
        .scalar()
        or 0
    )


def _last_sent(db: Session, account: EmailAccount) -> datetime | None:
    return (
        db.query(func.max(WarmupMessage.sent_at)).filter(WarmupMessage.from_account_id == account.id).scalar()
    )


def _name(account: EmailAccount) -> str:
    name = (account.display_name or "").strip()
    return name.split()[0] if name and "@" not in name else ""


def _body(to: EmailAccount, sender: EmailAccount) -> str:
    greeting = f"Hi {_name(to)}," if _name(to) else "Hi,"
    middle = " ".join(random.sample(MIDDLES, 2))
    sign = _name(sender)
    return f"{greeting}\n\n{random.choice(OPENERS)} {middle}\n\n{random.choice(CLOSERS)}\n{sign}".rstrip()


def _html(text: str) -> str:
    from html import escape

    return "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in text.split("\n\n") if p.strip())


def send_warmup(db: Session, sender: EmailAccount, to: EmailAccount, *, reply_to: WarmupMessage | None = None) -> None:
    from app.services.tracking import new_rfc_message_id

    rfc_message_id = new_rfc_message_id(sender.email_address)
    headers = {"Message-ID": rfc_message_id}
    if reply_to:
        subject = reply_to.subject if reply_to.subject.lower().startswith("re:") else f"Re: {reply_to.subject}"
        text = f"{random.choice(REPLIES)}\n\n{_name(sender)}".rstrip()
        headers["In-Reply-To"] = reply_to.rfc_message_id
        headers["References"] = reply_to.rfc_message_id
    else:
        subject = random.choice(SUBJECTS)
        text = _body(to, sender)
    send_gmail_message(db, sender, to=to.email_address, subject=subject, html_body=_html(text), text_body=text, headers=headers)
    db.add(
        WarmupMessage(
            user_id=sender.user_id,
            from_account_id=sender.id,
            to_account_id=to.id,
            rfc_message_id=rfc_message_id,
            in_reply_to=reply_to.rfc_message_id if reply_to else "",
            subject=subject,
            sent_at=datetime.now(UTC),
        )
    )
    db.commit()


def handle_received(db: Session, account: EmailAccount) -> int:
    """For warm-up emails this inbox received: rescue from spam, mark read + important, file away, sometimes reply."""
    pending = (
        db.query(WarmupMessage)
        .filter(
            WarmupMessage.to_account_id == account.id,
            WarmupMessage.handled_at.is_(None),
            WarmupMessage.sent_at <= datetime.now(UTC) - HANDLE_AFTER,
        )
        .order_by(WarmupMessage.sent_at.asc())
        .limit(20)
        .all()
    )
    if not pending:
        return 0
    handled = 0
    with _imap(account) as imap:
        inbox_folder = "INBOX"
        for item in pending:
            landed, folder, uids = locate(imap, item.rfc_message_id)
            if landed == "missing":
                if item.sent_at < datetime.now(UTC) - timedelta(hours=6):
                    item.landed, item.handled_at = "missing", datetime.now(UTC)
                continue
            if landed == "spam":
                _move(imap, uids, inbox_folder)  # "Not spam": the strongest positive signal
                _, folder, uids = locate(imap, item.rfc_message_id)  # now in the inbox
                if not uids:
                    item.landed, item.handled_at = "spam", datetime.now(UTC)
                    continue
            joined = b",".join(uids).decode()
            imap.select(f'"{folder}"')
            imap.uid("STORE", joined, "+FLAGS", "(\\Seen)")
            try:
                imap.uid("STORE", joined, "+X-GM-LABELS", f'(\\Important "{WARMUP_LABEL}")')
                imap.uid("STORE", joined, "-X-GM-LABELS", "(\\Inbox)")  # filed under "Warm-up", out of your inbox
            except imaplib.IMAP4.error:
                pass  # not Gmail
            item.landed, item.handled_at = landed, datetime.now(UTC)
            handled += 1
            db.commit()
            if not item.in_reply_to and random.random() < REPLY_CHANCE:
                sender = db.get(EmailAccount, item.from_account_id)
                if sender and sender.status == "connected":
                    try:
                        send_warmup(db, account, sender, reply_to=item)
                        item.replied = True
                    except GmailSendError as exc:
                        logger.warning("warmup_reply_failed", account_id=account.id, error=str(exc)[:200])
        db.commit()
    return handled


def run_warmup(db: Session) -> dict:
    """One warm-up round for every user: process what arrived, then send what is due (spread through the day)."""
    sent = handled = 0
    user_ids = [row[0] for row in db.query(EmailAccount.user_id).filter(EmailAccount.status == "connected").distinct()]
    for user_id in user_ids:
        settings = _settings(db, user_id)
        if settings is not None and not settings.warmup_enabled:
            continue
        accounts = warmup_accounts(db, user_id)
        for account in accounts:
            try:
                handled += handle_received(db, account)
            except Exception as exc:  # one inbox's IMAP problem must not stop the rest
                db.rollback()
                logger.warning("warmup_handle_failed", account_id=account.id, error=str(exc)[:200])
        if len(accounts) < 2 or not in_send_window(settings):
            continue
        window_hours = max(((settings.send_window_end - settings.send_window_start) % 24) if settings else 11, 1)
        for account in accounts:
            target = daily_target(account)
            if _sent_today(db, account) >= target:
                continue
            last = _last_sent(db, account)
            gap = timedelta(hours=window_hours / target) * random.uniform(0.6, 1.2)
            if last and datetime.now(UTC) - last < gap:
                continue
            to = random.choice([a for a in accounts if a.id != account.id])
            try:
                send_warmup(db, account, to)
                sent += 1
            except GmailSendError as exc:
                logger.warning("warmup_send_failed", account_id=account.id, error=str(exc)[:200])
    if sent or handled:
        logger.info("warmup_round", sent=sent, handled=handled)
    return {"sent": sent, "handled": handled}


def warmup_stats(db: Session, account: EmailAccount) -> dict:
    since = datetime.now(UTC) - timedelta(days=7)
    rows = (
        db.query(WarmupMessage.landed, func.count(WarmupMessage.id))
        .filter(WarmupMessage.from_account_id == account.id, WarmupMessage.sent_at >= since, WarmupMessage.landed != "")
        .group_by(WarmupMessage.landed)
        .all()
    )
    counts = dict(rows)
    return {
        "sent_today": _sent_today(db, account),
        "daily_target": daily_target(account),
        "last_7_days": {k: counts.get(k, 0) for k in ("inbox", "promotions", "spam", "missing")},
    }


# ---------------------------------------------------------------- domain check (SPF / DKIM / DMARC)

FREE_MAIL = {"gmail.com", "googlemail.com"}


def _txt(name: str) -> list[str] | None:
    answer = dns_query(name, "TXT")
    if answer is None:
        return None
    return [str(r.get("data", "")).replace('" "', "").strip('"') for r in answer.get("Answer") or [] if r.get("type") == 16]


def domain_check(db: Session, account: EmailAccount) -> dict:
    domain = account.email_address.rsplit("@", 1)[-1].lower()
    now = datetime.now(UTC).isoformat()
    if domain in FREE_MAIL:
        result = {
            "at": now,
            "domain": domain,
            "managed_by_google": True,
            "summary": "@gmail.com addresses are signed by Google (SPF, DKIM and DMARC are handled for you).",
            "checks": [],
        }
    else:
        spf = _txt(domain)
        dkim = _txt(f"google._domainkey.{domain}")
        dmarc = _txt(f"_dmarc.{domain}")
        spf_record = next((t for t in spf or [] if t.lower().startswith("v=spf1")), "")
        dmarc_record = next((t for t in dmarc or [] if t.lower().startswith("v=dmarc1")), "")
        checks = [
            {
                "name": "SPF",
                "ok": bool(spf_record) and ("_spf.google.com" in spf_record or "include:" in spf_record),
                "detail": spf_record or "Missing. Add a TXT record: v=spf1 include:_spf.google.com ~all",
            },
            {
                "name": "DKIM",
                "ok": any("v=dkim1" in t.lower() or "p=" in t for t in dkim or []),
                "detail": "Found (selector google)." if dkim else "Not found at google._domainkey. Turn on DKIM in the Google Admin console (Gmail -> Authenticate email).",
            },
            {
                "name": "DMARC",
                "ok": bool(dmarc_record),
                "detail": dmarc_record or "Missing. Add a TXT record at _dmarc: v=DMARC1; p=none; rua=mailto:you@yourdomain",
            },
        ]
        if spf is None and dkim is None and dmarc is None:
            summary = "Couldn't reach DNS. Try again in a minute."
        else:
            ok = sum(1 for c in checks if c["ok"])
            summary = f"{ok} of 3 set up correctly." + ("" if ok == 3 else " Missing records send more email to spam.")
        result = {"at": now, "domain": domain, "managed_by_google": False, "summary": summary, "checks": checks}
    account.health = {**(account.health or {}), "domain": result}
    db.commit()
    return result


# ---------------------------------------------------------------- spam placement test

PLACEMENT_WAIT_SECONDS = 90


def _sample_email(db: Session, account: EmailAccount) -> tuple[str, str, list[EmailAttachment]]:
    """Your latest real outreach email (with its PDF when it had one), so the test matches what leads receive."""
    last = (
        db.query(EmailMessage)
        .filter(
            EmailMessage.user_id == account.user_id,
            EmailMessage.status == "sent",
            EmailMessage.followup_of_id.is_(None),
            EmailMessage.is_test.is_(False),
            EmailMessage.html_body != "",
        )
        .order_by(EmailMessage.sent_at.desc())
        .first()
    )
    if not last:
        return (
            "A quick idea for your website",
            "<p>Hi,</p><p>I had a look at your website and noticed a couple of quick wins on the homepage. "
            "Happy to share them if useful.</p><p>Best,</p>",
            [],
        )
    attachments = []
    audit = db.query(WebsiteAudit).filter(WebsiteAudit.email_message_id == last.id).first()
    if audit and audit.pdf_data:
        attachments = [EmailAttachment(filename=audit.pdf_filename or "website-audit.pdf", content=audit.pdf_data)]
    return last.subject, last.html_body, attachments


def run_placement_test(user_id: str, account_id: str) -> None:
    """Send your latest outreach email from this inbox to your other inboxes, then see where it landed."""
    from app.database import get_session
    from app.services.tracking import new_rfc_message_id

    db = get_session()
    try:
        account = db.get(EmailAccount, account_id)
        receivers = [a for a in warmup_accounts(db, user_id) if a.id != account_id] or [
            a
            for a in db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.status == "connected")
            if a.id != account_id and _smtp_payload(a)
        ]
        subject, html, attachments = _sample_email(db, account)
        sent = []
        for to in receivers:
            rfc = new_rfc_message_id(account.email_address)
            try:
                send_gmail_message(
                    db, account, to=to.email_address, subject=subject, html_body=html, attachments=attachments,
                    headers={"Message-ID": rfc},
                )
                sent.append((to, rfc))
            except GmailSendError as exc:
                logger.warning("placement_send_failed", account_id=account_id, to=to.id, error=str(exc)[:200])
        time.sleep(PLACEMENT_WAIT_SECONDS)
        results = []
        for to, rfc in sent:
            landed = "missing"
            try:
                with _imap(to) as imap:
                    for _ in range(3):
                        landed, folder, uids = locate(imap, rfc)
                        if landed != "missing":
                            break
                        time.sleep(30)
                    if uids:  # tidy up: the test email goes to the bin
                        trash = _special_folder(imap, "\\Trash", "[Gmail]/Trash")
                        imap.select(f'"{folder}"')
                        _move(imap, uids, trash)
            except Exception as exc:
                landed = "error"
                logger.warning("placement_check_failed", account_id=to.id, error=str(exc)[:200])
            results.append({"to": to.email_address, "landed": landed})
        account = db.get(EmailAccount, account_id)
        account.health = {
            **(account.health or {}),
            "placement": {"at": datetime.now(UTC).isoformat(), "running": False, "subject": subject, "results": results},
        }
        db.commit()
        logger.info("placement_test_done", account_id=account_id, results=results)
    except Exception as exc:
        db.rollback()
        account = db.get(EmailAccount, account_id)
        if account:
            account.health = {
                **(account.health or {}),
                "placement": {"at": datetime.now(UTC).isoformat(), "running": False, "error": str(exc)[:300], "results": []},
            }
            db.commit()
        logger.error("placement_test_failed", account_id=account_id, error=str(exc)[:200])
    finally:
        db.close()


def start_placement_test(db: Session, account: EmailAccount) -> dict:
    others = [
        a
        for a in db.query(EmailAccount).filter(EmailAccount.user_id == account.user_id, EmailAccount.status == "connected")
        if a.id != account.id and _smtp_payload(a)
    ]
    if not others:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="The test sends to your other inboxes and checks where it lands. Connect a second Gmail (SMTP) first.",
        )
    account.health = {
        **(account.health or {}),
        "placement": {"at": datetime.now(UTC).isoformat(), "running": True, "results": []},
    }
    db.commit()
    threading.Thread(target=run_placement_test, args=(account.user_id, account.id), daemon=True).start()
    return {"started": True, "receivers": len(others)}

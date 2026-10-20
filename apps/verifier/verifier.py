"""Own mailbox checker: asks the recipient's mail server whether a mailbox exists, without sending anything.

Runs on a small rented server with outbound port 25 open (home connections can't do this). The lead-generation
app calls it before an audit / email (AI & settings -> Sending -> Email verification -> Own checker).

How a check works (an SMTP conversation that stops before any message):
  1. look up the domain's mail servers (MX)
  2. connect on port 25, EHLO, STARTTLS when offered, MAIL FROM:<your checking address>
  3. RCPT TO:<the address>         -> the server says whether it would accept mail for it
  4. RCPT TO:<a random address>    -> if that is accepted too, the domain accepts everything ("accept_all"),
                                      so the first answer proves nothing
  5. QUIT                          -> nothing is ever sent

Results: valid / invalid / accept_all / unknown. The app only stops an email on "invalid".
  - Google Workspace answers "OK" for every address on a business domain: those come back accept_all
  - Microsoft 365 rejects addresses that don't exist (550 5.4.1 / 5.1.10) unless it blocks this server
  - "try again later" (greylisting) or a block of this server = unknown, never invalid

Being a good citizen (so this server isn't blocked): one connection at a time per mail server, a pause between
them, results cached for 14 days, servers that block us skipped for a day.

Run:  VERIFIER_TOKEN=... HELO_NAME=check.example.com MAIL_FROM=verify@example.com \
      uvicorn verifier:app --host 127.0.0.1 --port 8090
"""

from __future__ import annotations

import os
import random
import re
import smtplib
import socket
import string
import threading
import time
from dataclasses import dataclass

import dns.resolver
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN = os.environ.get("VERIFIER_TOKEN", "")
HELO_NAME = os.environ.get("HELO_NAME", socket.getfqdn())
MAIL_FROM = os.environ.get("MAIL_FROM", f"verify@{HELO_NAME}")
TIMEOUT = float(os.environ.get("SMTP_TIMEOUT", "15"))
HOST_GAP = float(os.environ.get("HOST_GAP_SECONDS", "8"))  # pause between two checks on the same mail server
CACHE_SECONDS = 14 * 24 * 3600
BLOCKED_SECONDS = 24 * 3600

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NO_SUCH_USER = re.compile(
    r"(5\.1\.1|5\.1\.10|5\.4\.1|does not exist|doesn't exist|no such user|user unknown|unknown user|"
    r"recipient not found|recipient address rejected|mailbox not found|mailbox unavailable|invalid recipient|"
    r"no mailbox|not a valid mailbox|address rejected|unknown recipient|undeliverable address|user not found)",
    re.I,
)
BLOCKED_US = re.compile(
    r"(5\.7\.|spamhaus|blocked|blacklist|block list|blocklist|banned|reputation|policy|denied by|not allowed|"
    r"rbl|dnsbl|client host|too many|rate limit)",
    re.I,
)


@dataclass
class Result:
    status: str  # valid | invalid | accept_all | unknown
    detail: str
    mx: str = ""

    def as_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail, "mx": self.mx}


def classify(code: int, message: str) -> str:
    """One RCPT answer -> accepted | no_such_user | blocked | temporary | unknown."""
    text = message or ""
    if code in (250, 251):
        return "accepted"
    if 400 <= code < 500:
        return "temporary"  # greylisting, rate limit, mailbox busy: can't tell
    if code >= 500:
        # Microsoft's "5.4.1 Recipient address rejected: Access denied" is its directory check (no such user);
        # other 5.7.x answers are about us being blocked, not about the mailbox
        if re.search(r"5\.4\.1|5\.1\.1|5\.1\.10", text):
            return "no_such_user"
        if BLOCKED_US.search(text):
            return "blocked"
        if NO_SUCH_USER.search(text) or code in (550, 551, 553):
            return "no_such_user" if NO_SUCH_USER.search(text) else "unknown"
    return "unknown"


# ---------------------------------------------------------------- caches and pacing

_lock = threading.Lock()
_cache: dict[str, tuple[float, Result]] = {}
_blocked: dict[str, float] = {}
_host_last: dict[str, float] = {}
_host_locks: dict[str, threading.Lock] = {}


def _host_lock(host: str) -> threading.Lock:
    with _lock:
        return _host_locks.setdefault(host, threading.Lock())


def mx_hosts(domain: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        rows = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        return [host for _, host in rows if host and host != "."]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        try:
            dns.resolver.resolve(domain, "A", lifetime=8)
            return [domain]  # no MX: mail goes to the domain itself
        except Exception:
            return []
    except Exception:
        return []


def _random_address(domain: str) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=14)) + f"@{domain}"


def _conversation(host: str, email: str, domain: str) -> Result:
    smtp = smtplib.SMTP(timeout=TIMEOUT)
    try:
        smtp.connect(host, 25)
        smtp.ehlo(HELO_NAME)
        if smtp.has_extn("starttls"):
            try:
                smtp.starttls()
                smtp.ehlo(HELO_NAME)
            except Exception:
                smtp.close()
                smtp = smtplib.SMTP(timeout=TIMEOUT)
                smtp.connect(host, 25)
                smtp.ehlo(HELO_NAME)
        code, message = smtp.mail(MAIL_FROM)
        if code >= 400:
            kind = classify(code, message.decode(errors="replace"))
            return Result("unknown", f"sender refused ({code}): {message[:120]!r}", host) if kind != "blocked" else \
                Result("unknown", f"blocked: {message[:120]!r}", host)
        code, message = smtp.rcpt(email)
        text = message.decode(errors="replace")
        kind = classify(code, text)
        if kind == "no_such_user":
            return Result("invalid", f"{code} {text[:160]}", host)
        if kind == "blocked":
            return Result("unknown", f"blocked: {code} {text[:160]}", host)
        if kind != "accepted":
            return Result("unknown", f"{code} {text[:160]}", host)
        code2, _ = smtp.rcpt(_random_address(domain))
        if code2 in (250, 251):
            return Result("accept_all", "the domain accepts every address, so this one can't be confirmed", host)
        return Result("valid", f"{code} {text[:160]}", host)
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()


def check(email: str) -> Result:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return Result("invalid", "not an email address")
    now = time.time()
    with _lock:
        hit = _cache.get(email)
        if hit and hit[0] > now:
            return hit[1]
    domain = email.rsplit("@", 1)[1]
    hosts = mx_hosts(domain)
    if not hosts:
        return Result("invalid", "the domain has no mail server")
    result = Result("unknown", "no mail server answered")
    for host in hosts[:2]:
        if _blocked.get(host, 0) > now:
            result = Result("unknown", "this mail server blocks checks from this server (skipped for a day)", host)
            continue
        with _host_lock(host):
            wait = _host_last.get(host, 0) + HOST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                result = _conversation(host, email, domain)
            except (socket.timeout, TimeoutError, ConnectionRefusedError, OSError, smtplib.SMTPException) as exc:
                result = Result("unknown", f"{type(exc).__name__}: {str(exc)[:120]}", host)
            finally:
                _host_last[host] = time.time()
        if result.detail.startswith("blocked"):
            _blocked[host] = time.time() + BLOCKED_SECONDS
        if result.status != "unknown":
            break
    if result.status != "unknown":
        with _lock:
            _cache[email] = (time.time() + CACHE_SECONDS, result)
    return result


# ---------------------------------------------------------------- API

app = FastAPI(title="Mailbox checker")


class VerifyIn(BaseModel):
    email: str


def _auth(authorization: str | None) -> None:
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="Wrong or missing token")


@app.get("/status")
def status(authorization: str | None = Header(default=None)) -> dict:
    """For the app's "Test" button: the token works, and whether this server can reach mail servers on port 25."""
    _auth(authorization)
    try:
        socket.create_connection(("gmail-smtp-in.l.google.com", 25), timeout=8).close()
        port25 = True
    except OSError:
        port25 = False
    return {"ok": True, "port25": port25, "helo": HELO_NAME, "mail_from": MAIL_FROM, "cached": len(_cache)}


@app.post("/verify")
def verify(data: VerifyIn, authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"email": data.email.strip().lower(), **check(data.email).as_dict()}

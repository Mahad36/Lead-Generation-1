"""Account health protection: health always overrides sending limits.

Unit tests for the decision (order, policies, acknowledged warnings), and integration tests on a real, throwaway
PostgreSQL (pgserver) for the sending pipeline itself: rotation, reassignment, holds, campaigns, follow-ups, and the
atomic capacity reservation under concurrent workers. The integration tests are skipped if pgserver can't start.
"""

import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import send_protection
from app.services.send_protection import HOLDS, Verdict, decide

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def acct(**extra):
    base = dict(id="a1", user_id="u1", email_address="me@gmail.com", status="connected", health_status="healthy",
                health_reason="", paused_reason="", restricted_until=None, daily_limit=100)
    return SimpleNamespace(**{**base, **extra})


# ---------------------------------------------------------------- the decision (pure)


def test_health_overrides_unused_capacity():
    # Limit 100, 40 sent, 60 left: a credible warning sign still blocks the inbox
    v = decide(acct(), holds=["Gmail blocked messages as against its policy"], assessed=True,
               sent_today=40, in_flight=0, safe_capacity=100, now=NOW)
    assert not v.ok and v.stage == "health" and v.safety_block
    assert v.as_dict()["remaining_configured"] == 60  # shown on the dashboard, but not used


def test_decision_order_health_provider_safe_capacity_user_limit():
    paused = acct(paused_reason="Paused automatically: too many bounces")
    # 1. health comes before everything
    assert decide(paused, holds=["x"], assessed=True, sent_today=0, in_flight=0, safe_capacity=10, now=NOW).stage == "health"
    # 2. provider restrictions
    assert decide(paused, holds=[], assessed=True, sent_today=0, in_flight=0, safe_capacity=10, now=NOW).stage == "provider"
    restricted = acct(restricted_until=NOW + timedelta(hours=2), health_reason="Gmail's daily quota was hit.")
    assert decide(restricted, holds=[], assessed=True, sent_today=0, in_flight=0, safe_capacity=10, now=NOW).stage == "provider"
    assert decide(acct(health_status="reconnect"), holds=[], assessed=True, sent_today=0, in_flight=0,
                  safe_capacity=10, now=NOW).stage == "provider"
    # 3. effective safe capacity (below your limit)
    v = decide(acct(), holds=[], assessed=True, sent_today=17, in_flight=0, safe_capacity=17, now=NOW)
    assert v.stage == "safe_capacity" and not v.safety_block
    assert decide(acct(), holds=[], assessed=True, sent_today=0, in_flight=0, safe_capacity=0, now=NOW).stage == "safe_capacity"
    # 4. your limit
    assert decide(acct(daily_limit=30), holds=[], assessed=True, sent_today=30, in_flight=0, safe_capacity=30,
                  now=NOW).stage == "user_limit"
    # all clear
    assert decide(acct(), holds=[], assessed=True, sent_today=5, in_flight=0, safe_capacity=30, now=NOW).ok


def test_unassessable_health_holds_sending():
    v = decide(acct(), holds=[], assessed=False, sent_today=0, in_flight=0, safe_capacity=100, now=NOW)
    assert not v.ok and v.stage == "health"


def test_emails_in_flight_count_against_capacity():
    v = decide(acct(daily_limit=10), holds=[], assessed=True, sent_today=9, in_flight=1, safe_capacity=10, now=NOW)
    assert not v.ok and v.stage == "user_limit"


def test_policies():
    assert "status:warning" in HOLDS["strict"] and "status:warning" not in HOLDS["balanced"]
    assert "blacklist:critical" in HOLDS["balanced"]
    for policy in HOLDS.values():  # weak or configuration-only signs never hold sending on their own
        assert not {"volume:warning", "no_replies:warning", "dns_dmarc:warning"} & policy


# ---------------------------------------------------------------- integration (real PostgreSQL)


@pytest.fixture(scope="module")
def pg():
    try:
        import pgserver
        import sqlalchemy as sa
        from sqlalchemy.orm import sessionmaker

        from app.database import Base
        import app.models  # noqa: F401

        server = pgserver.get_server(tempfile.mkdtemp(prefix="sendprot_"), cleanup_mode="delete")
        engine = sa.create_engine(server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1))
        Base.metadata.create_all(engine)
    except Exception as exc:  # pragma: no cover - no embedded Postgres here
        pytest.skip(f"embedded PostgreSQL unavailable: {exc}")
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(pg, monkeypatch):
    from sqlalchemy import text

    import app.database

    session = pg()
    for table in ("audit_logs", "health_alerts", "email_messages", "campaign_leads", "campaigns", "leads",
                  "email_accounts", "user_settings", "users"):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    monkeypatch.setattr(app.database, "get_session", pg)  # the audit trail's own sessions
    yield session
    session.close()


def make_user(db, policy="strict"):
    from app.models import User, UserSettings

    user = User(email=f"u{time.time_ns()}@x.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(UserSettings(user_id=user.id, health_policy=policy))
    db.commit()
    return user


def make_account(db, user, address, *, status="healthy", reason="", limit=100, **extra):
    from app.models import EmailAccount

    account = EmailAccount(user_id=user.id, email_address=address, status="connected", provider="smtp",
                           daily_limit=limit, health_status=status, health_reason=reason,
                           health_checked_at=datetime.now(UTC), health_signals={}, health={}, **extra)
    db.add(account)
    db.commit()
    return account


def sent(db, account, n, **extra):
    from app.models import EmailMessage

    now = datetime.now(UTC)
    for i in range(n):
        db.add(EmailMessage(user_id=account.user_id, email_account_id=account.id, recipient_email=f"r{i}@x.com",
                            subject="s", status="sent", sent_at=now - timedelta(seconds=150 + i), **extra))
    db.commit()


def events(db, action):
    from app.models import AuditLog

    return db.query(AuditLog).filter(AuditLog.action == action).all()


def test_unhealthy_account_with_capacity_is_not_used(db):
    from app.services.deliverability import pick_sending_account

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced",
                         reason="Sending halved: bounce rate 4% of the last 50 emails.", limit=100, is_default=True)
    sent(db, risky, 40)  # 60 left of its limit
    v = send_protection.evaluate(db, risky)
    assert v.stage == "health" and v.as_dict()["remaining_configured"] == 60
    assert pick_sending_account(db, user.id) is None  # rotation never picks it


def test_rotation_switches_to_an_independently_healthy_account(db):
    from app.services.deliverability import sending_account

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.")
    good = make_account(db, user, "good@gmail.com")
    chosen = sending_account(db, user.id, risky.id, automatic=True)
    assert chosen.id == good.id
    moved = events(db, "sending.reassigned")
    assert moved and moved[0].details["to"] == "good@gmail.com" and moved[0].details["reason"]


def test_alternative_must_pass_every_check_itself(db):
    from app.models import HealthAlert
    from app.services.deliverability import SendDeferred, sending_account

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.")
    full = make_account(db, user, "full@gmail.com", limit=5)
    sent(db, full, 5)  # healthy but no capacity left
    listed = make_account(db, user, "me@mydomain.com")
    db.add(HealthAlert(user_id=user.id, key="blacklist:mydomain.com", kind="blacklist", severity="critical",
                       subject="mydomain.com", title="mydomain.com is on a spam blacklist", last_seen_at=datetime.now(UTC)))
    db.commit()
    assert send_protection.evaluate(db, listed).stage == "health"  # its domain is blacklisted
    with pytest.raises(SendDeferred) as held:
        sending_account(db, user.id, risky.id, automatic=True)
    assert held.value.step == send_protection.WAITING_FOR_HEALTH
    assert not events(db, "sending.reassigned")


def test_gmail_restrictions_are_never_moved_to_another_account(db):
    from app.services.deliverability import SendDeferred, sending_account

    user = make_user(db)
    quota = make_account(db, user, "quota@gmail.com", restricted_until=datetime.now(UTC) + timedelta(hours=20),
                         reason="Gmail says this account reached its daily sending limit (quota).")
    make_account(db, user, "good@gmail.com")
    with pytest.raises(SendDeferred) as held:
        sending_account(db, user.id, quota.id, automatic=True)
    assert held.value.step == send_protection.WAITING_FOR_HEALTH and "quota" in str(held.value.detail)


def test_no_safe_account_holds_the_email(db):
    from app.services.deliverability import SendDeferred, sending_account

    user = make_user(db)
    make_account(db, user, "a@gmail.com", status="reduced", reason="Sending halved: bounces.")
    make_account(db, user, "b@gmail.com", paused_reason="Paused automatically: too many bounces.")
    with pytest.raises(SendDeferred) as held:
        sending_account(db, user.id, None, automatic=True)
    assert held.value.step == send_protection.WAITING_FOR_HEALTH
    assert held.value.step in __import__("app.services.website_audit", fromlist=["x"]).WAITING_STEPS  # retried


def test_guard_blocks_and_records_it(db):
    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.")
    with pytest.raises(send_protection.HealthHold) as held:
        send_protection.guard(db, risky)
    assert held.value.verdict.stage == "health"
    blocked = events(db, "sending.blocked")
    assert blocked and "bounces" in blocked[0].details["reason"]
    db.refresh(risky)
    assert risky.health["protection"]["blocked"] and risky.health["protection"]["since"]


def test_acknowledged_warning_stops_holding_but_critical_never_does(db):
    from app.models import HealthAlert

    user = make_user(db)
    account = make_account(db, user, "a@gmail.com", status="reduced", reason="Sending halved: bounces.")
    assert send_protection.evaluate(db, account).stage == "health"
    db.add(HealthAlert(user_id=user.id, email_account_id=account.id, key=f"status:{account.id}", kind="status",
                       severity="warning", title="t", last_seen_at=datetime.now(UTC), acknowledged_at=datetime.now(UTC)))
    db.commit()
    assert send_protection.evaluate(db, account).ok
    account.health_signals = {"rejected_7d": 2}  # Gmail refused messages: critical, can't be acknowledged
    db.commit()
    assert send_protection.evaluate(db, account).stage == "health"


def test_balanced_policy_only_holds_on_serious_signs(db):
    user = make_user(db, policy="balanced")
    account = make_account(db, user, "a@gmail.com", status="reduced", reason="Sending halved: bounces.")
    assert send_protection.evaluate(db, account).ok


def test_capacity_reservation_is_atomic_under_concurrent_workers(db, pg):
    from app.models import EmailAccount, EmailMessage

    user = make_user(db)
    account = make_account(db, user, "a@gmail.com", limit=5)
    sent(db, account, 4)  # one slot left
    results = []

    def worker():
        session = pg()
        try:
            row = session.get(EmailAccount, account.id)
            send_protection.guard(session, row, spacing=False)
            time.sleep(0.5)  # the other worker is waiting at the lock meanwhile
            session.add(EmailMessage(user_id=user.id, email_account_id=row.id, recipient_email="x@x.com",
                                     subject="s", status="sending"))
            session.commit()
            results.append("sent")
        except send_protection.HealthHold as hold:
            results.append(hold.verdict.stage)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == ["sent", "user_limit"]


def test_spacing_counts_emails_still_being_sent(db):
    from app.models import EmailMessage

    user = make_user(db)
    account = make_account(db, user, "a@gmail.com")
    db.add(EmailMessage(user_id=user.id, email_account_id=account.id, recipient_email="x@x.com", subject="s",
                        status="sending"))
    db.commit()
    with pytest.raises(send_protection.HealthHold) as held:
        send_protection.guard(db, account)
    assert held.value.verdict.stage == "spacing"


# ---------------------------------------------------------------- campaigns and follow-ups


def campaign_with_lead(db, user, account, rotate=False):
    from app.models import Campaign, CampaignLead, Lead

    lead = Lead(user_id=user.id, business_name="Acme Dental", primary_email="info@acme-dental.com",
                website_url="https://acme-dental.com")
    db.add(lead)
    db.flush()
    campaign = Campaign(user_id=user.id, name="Test", email_account_id=account.id, status="running",
                        daily_limit=50, hourly_limit=50, extra={"rotate": rotate, "rotation": {"mode": "auto" if rotate else "single", "account_ids": []}})
    db.add(campaign)
    db.flush()
    cl = CampaignLead(campaign_id=campaign.id, lead_id=lead.id, status="sending", subject="Hello",
                      html_body="<p>Hi</p>", text_body="Hi")
    db.add(cl)
    db.commit()
    return campaign, cl


@pytest.fixture
def no_network(monkeypatch):
    from app.services import campaigns

    sends = []
    monkeypatch.setattr(campaigns, "address_problem", lambda *a, **k: "")
    monkeypatch.setattr(campaigns, "account_signature", lambda account: ("", ""))
    monkeypatch.setattr(campaigns, "send_gmail_message", lambda db, account, **k: sends.append(account.email_address) or "id")
    return sends


def test_campaign_holds_instead_of_sending(db, no_network):
    from app.models import Campaign, CampaignLead
    from app.services.campaigns import send_claimed_lead

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.")
    campaign, cl = campaign_with_lead(db, user, risky)
    assert send_claimed_lead(db, cl) == "health_hold"
    assert no_network == []
    campaign, cl = db.get(Campaign, campaign.id), db.get(CampaignLead, cl.id)
    db.refresh(campaign), db.refresh(cl)
    assert campaign.status == "paused" and campaign.paused_reason == "account_health_protection"
    assert "Account Health Protection" in campaign.error_message and cl.status == "queued"


def test_campaign_moves_to_a_healthy_account_and_resumes_by_itself(db, no_network):
    from app.models import Campaign
    from app.services.campaigns import resume_health_held_campaigns, send_claimed_lead

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.")
    campaign, cl = campaign_with_lead(db, user, risky)
    assert send_claimed_lead(db, cl) == "health_hold"
    good = make_account(db, user, "good@gmail.com")  # a safe inbox becomes available

    import app.services.scraping_jobs as jobs

    queued = []
    jobs.enqueue_task, original = (lambda task, *a: queued.append(a)), jobs.enqueue_task
    try:
        assert resume_health_held_campaigns(db) == 1
    finally:
        jobs.enqueue_task = original
    db.refresh(campaign)
    assert campaign.status == "running" and queued
    from app.models import CampaignLead

    cl = db.get(CampaignLead, cl.id)
    cl.status = "sending"
    db.commit()
    assert send_claimed_lead(db, cl) == "sent"
    assert no_network == [good.email_address]
    assert events(db, "sending.reassigned")


def test_followups_stay_on_their_inbox_and_wait(db, monkeypatch):
    from app.models import EmailMessage
    from app.services import followups

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.")
    make_account(db, user, "good@gmail.com")
    root = EmailMessage(user_id=user.id, email_account_id=risky.id, recipient_email="x@x.com", subject="s",
                        status="sent", sent_at=datetime.now(UTC) - timedelta(days=4))
    db.add(root)
    db.commit()
    calls = []
    monkeypatch.setattr(followups, "due_followups", lambda db: [(root, 1, 2)])
    monkeypatch.setattr(followups, "_on_hold", lambda db, root: False)
    monkeypatch.setattr(followups, "_send_time_ok", lambda db, root: True)
    monkeypatch.setattr(followups, "send_followup", lambda *a: calls.append(a))
    assert followups.run_followups(db) == {"sent": 0, "waiting": 1}
    assert calls == []  # never sent from another inbox either (the thread stays together)


def test_protection_round_records_recovery(db):
    user = make_user(db)
    account = make_account(db, user, "a@gmail.com", status="reduced", reason="Sending halved: bounces.")
    send_protection.protection_round(db)
    assert events(db, "sending.blocked")
    account.health_status, account.health_reason = "healthy", ""
    db.commit()
    send_protection.protection_round(db)
    assert events(db, "sending.allowed")


def test_verdict_dict_for_the_dashboard():
    v = Verdict(False, "health", "why", 100, 50, 40, 1)
    d = v.as_dict()
    assert d["remaining_configured"] == 59 and d["remaining_safe"] == 9 and d["eligible"] is False


def test_rotating_campaign_skips_the_unhealthy_inbox(db, no_network):
    from app.services.campaigns import send_claimed_lead

    user = make_user(db)
    risky = make_account(db, user, "risky@gmail.com", status="reduced", reason="Sending halved: bounces.", is_default=True)
    good = make_account(db, user, "good@gmail.com")
    campaign, cl = campaign_with_lead(db, user, risky, rotate=True)
    assert send_claimed_lead(db, cl) == "sent"
    assert no_network == [good.email_address]


# ---------------------------------------------------------------- rotation and delays


def test_each_send_reserves_a_random_delay_for_that_inbox(db):
    user = make_user(db)
    account = make_account(db, user, "a@gmail.com")
    before = datetime.now(UTC)
    send_protection.guard(db, account)  # passes, and reserves this inbox's next gap
    db.commit()
    gap = (account.next_send_at - before).total_seconds()
    assert 180 <= gap <= 421  # default 3-7 minutes
    with pytest.raises(send_protection.HealthHold) as held:
        send_protection.guard(db, account)  # the next automatic email waits for it
    assert held.value.verdict.stage == "spacing" and held.value.verdict.ready_at >= account.next_send_at
    send_protection.guard(db, account, spacing=False)  # a manual send goes straight away (and reserves again)


def test_delay_never_below_gmails_minimum_gap(db):
    user = make_user(db)
    account = make_account(db, user, "a@gmail.com")
    send_protection.guard(db, account, delay=(5, 10))
    db.commit()
    assert (account.next_send_at - datetime.now(UTC)).total_seconds() >= 115


def test_rotation_prefers_an_inbox_that_is_ready_now(db):
    from app.services.deliverability import pick_sending_account

    user = make_user(db)
    busy = make_account(db, user, "busy@gmail.com", limit=100, next_send_at=datetime.now(UTC) + timedelta(minutes=4))
    ready = make_account(db, user, "ready@gmail.com", limit=20)
    assert pick_sending_account(db, user.id).id == ready.id  # even though busy has more room
    ready.next_send_at = datetime.now(UTC) + timedelta(minutes=6)
    db.commit()
    assert pick_sending_account(db, user.id).id == busy.id  # nobody ready: the one free soonest


def test_take_turns_mode_alternates(db):
    from app.services.deliverability import pick_sending_account

    user = make_user(db)
    a = make_account(db, user, "a@gmail.com", limit=100)
    b = make_account(db, user, "b@gmail.com", limit=10)
    order = []
    for _ in range(4):
        picked = pick_sending_account(db, user.id, mode="even")
        order.append(picked.email_address)
        send_protection.guard(db, picked, spacing=False)  # reserves its delay
        db.commit()
        for x in (a, b):  # fast-forward: both delays over, the order of use remembered
            if x.next_send_at:
                x.next_send_at = datetime.now(UTC) - timedelta(seconds=1 if x.id == picked.id else 60)
        db.commit()
    assert order == ["a@gmail.com", "b@gmail.com", "a@gmail.com", "b@gmail.com"] or order == [
        "b@gmail.com", "a@gmail.com", "b@gmail.com", "a@gmail.com"]


def test_inboxes_on_the_same_own_domain_keep_apart(db):
    from app.models import EmailMessage

    user = make_user(db)
    first = make_account(db, user, "mahad@beecreative.pk")
    second = make_account(db, user, "hello@beecreative.pk")
    other = make_account(db, user, "me@gmail.com")
    db.add(EmailMessage(user_id=user.id, email_account_id=first.id, recipient_email="x@x.com", subject="s",
                        status="sent", sent_at=datetime.now(UTC) - timedelta(seconds=10)))
    db.commit()
    with pytest.raises(send_protection.HealthHold) as held:
        send_protection.guard(db, second)
    assert held.value.verdict.stage == "spacing"
    send_protection.guard(db, other)  # a different domain isn't held


def test_rotating_campaign_is_paced_by_its_inboxes_not_one_delay(db, no_network):
    from app.models import Campaign
    from app.services.campaigns import send_claimed_lead

    user = make_user(db)
    first = make_account(db, user, "a@gmail.com", is_default=True)
    make_account(db, user, "b@gmail.com")
    make_account(db, user, "c@gmail.com")
    campaign, cl = campaign_with_lead(db, user, first, rotate=True)
    assert send_claimed_lead(db, cl) == "sent"
    campaign = db.get(Campaign, campaign.id)
    db.refresh(campaign)
    wait = (campaign.next_eligible_at - datetime.now(UTC)).total_seconds()
    assert wait < 60  # another inbox is ready: not 3-7 minutes for the whole campaign


def test_single_inbox_campaign_waits_for_that_inbox(db, no_network):
    from app.models import Campaign
    from app.services.campaigns import send_claimed_lead

    user = make_user(db)
    only = make_account(db, user, "a@gmail.com")
    campaign, cl = campaign_with_lead(db, user, only)
    assert send_claimed_lead(db, cl) == "sent"
    campaign = db.get(Campaign, campaign.id)
    db.refresh(campaign)
    wait = (campaign.next_eligible_at - datetime.now(UTC)).total_seconds()
    assert 55 <= wait <= 200  # the campaign's own 60-180 s range, floored at Gmail's 2 minutes


def test_scrape_file_rotation(db):
    from app.models import Lead, ScrapingJob, WebsiteAudit
    from app.services.website_audit import audit_file_rotation

    user = make_user(db)
    a = make_account(db, user, "a@gmail.com")
    job = ScrapingJob(user_id=user.id, name="f", status="completed",
                      extra={"rotation": {"mode": "selected", "account_ids": [a.id]}})
    db.add(job)
    db.flush()
    lead = Lead(user_id=user.id, business_name="B", scraping_job_id=job.id)
    db.add(lead)
    db.flush()
    audit = WebsiteAudit(user_id=user.id, lead_id=lead.id, trigger="auto", website_url="https://b.com")
    assert audit_file_rotation(db, audit) == {"mode": "selected", "account_ids": [a.id]}
    job.extra = {"rotation": {"mode": "even", "account_ids": []}}
    assert audit_file_rotation(db, audit)["mode"] == "even"
    job.extra = {}
    assert audit_file_rotation(db, audit) == {"mode": "auto", "account_ids": []}
    db.rollback()


def test_delay_validation():
    from app.services.limits import validate_send_limits

    assert validate_send_limits(30, 30, 180, 420)[2:] == (180, 420)
    assert validate_send_limits(30, 30, 5000, 9000)[2:] == (3600, 3600)
    assert validate_send_limits(30, 30, 400, 100)[2:] == (400, 400)


def test_automatic_audits_are_not_pinned_to_the_default_inbox(db, monkeypatch):
    """Regression: every scrape-file audit used to get the default inbox fixed at creation, so all emails went
    from one inbox and rotation never ran."""
    from app.models import Lead
    from app.services import scraping_jobs, website_audit

    monkeypatch.setattr(scraping_jobs, "enqueue_task", lambda *a, **k: "inline")
    user = make_user(db)
    make_account(db, user, "ahad@beecreative.pk", is_default=True)
    make_account(db, user, "hashir@beecreative.pk")
    lead = Lead(user_id=user.id, business_name="Smile", primary_email="info@smile.com", website_url="https://smile.com")
    db.add(lead)
    db.commit()
    audit = website_audit.start_audit(db, user.id, lead.id, auto_send=True, trigger="auto")
    assert audit.email_account_id is None  # rotation picks the inbox when the email is sent


# ---------------------------------------------------------------- audits pause when today's emails can't go out


def test_audit_budget_follows_sending_capacity(db):
    from app.models import Lead, WebsiteAudit

    user = make_user(db)
    a = make_account(db, user, "a@gmail.com", limit=5)
    b = make_account(db, user, "b@gmail.com", limit=5)
    sent(db, a, 5)  # a is full; b has 5 left
    assert send_protection.audit_budget(db, user.id)[0] == 5
    lead = Lead(user_id=user.id, business_name="X", primary_email="x@x.com")
    db.add(lead)
    db.flush()
    for status in ("ready", "ready", "analyzing"):  # audited / being audited, waiting for a slot
        db.add(WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url="https://x.com", auto_send=True, status=status))
    db.commit()
    assert send_protection.audit_budget(db, user.id)[0] == 2
    sent(db, b, 3)
    budget, reason = send_protection.audit_budget(db, user.id)
    assert budget == 0 and "used up" in reason and "Claude tokens" in reason
    assert send_protection.audit_budget(db, user.id, [a.id])[0] == 0  # a file that may only use the full inbox


def test_no_audits_while_no_inbox_is_safe(db):
    user = make_user(db)
    make_account(db, user, "a@gmail.com", status="reduced", reason="Sending halved: bounces.")
    budget, reason = send_protection.audit_budget(db, user.id)
    assert budget == 0 and send_protection.WAITING_FOR_HEALTH in reason


def test_file_audits_pause_when_sending_is_full_and_resume(db, monkeypatch):
    from app.models import Lead, ScrapingJob
    from app.services import auto_audits

    started = []
    monkeypatch.setattr(auto_audits, "outreach_issues", lambda *a, **k: [])
    monkeypatch.setattr(auto_audits, "address_problem", lambda *a, **k: "")
    monkeypatch.setattr(auto_audits, "start_audit", lambda db, user_id, lead_id, **k: started.append(lead_id))
    user = make_user(db)
    account = make_account(db, user, "a@gmail.com", limit=3)
    job = ScrapingJob(user_id=user.id, name="f", status="completed", outreach_status="running",
                      outreach_auto_send=True, extra={})
    db.add(job)
    db.flush()
    for i in range(5):
        db.add(Lead(user_id=user.id, business_name=f"B{i}", primary_email=f"b{i}@b{i}.com",
                    website_url=f"https://b{i}.com", scraping_job_id=job.id, status="ready"))
    db.commit()
    sent(db, account, 3)  # today's limit already reached
    assert auto_audits.poll_job(db, job) == 0 and started == []
    db.refresh(job)
    assert "used up" in job.extra["audit_hold"]
    assert "used up" in auto_audits.job_outreach_status(db, job)["audit_hold"]
    account.daily_limit = 5  # room again (e.g. the limits reset)
    db.commit()
    assert auto_audits.poll_job(db, job) == 2  # only as many as can still be sent today
    db.refresh(job)
    assert job.extra["audit_hold"] == ""


# ---------------------------------------------------------------- email verification (provider simulated)


@pytest.fixture
def verifier(db, monkeypatch):
    """A user with ZeroBounce set up; returns (user, calls, set_answer) with the provider simulated."""
    from app.models import UserSettings
    from app.services import email_verification
    from app.utils.crypto import encrypt_text

    user = make_user(db)
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    settings.email_verifier_provider = "zerobounce"
    settings.encrypted_email_verifier_key = encrypt_text("test-key")
    settings.email_verifier_monthly_limit = 3
    db.commit()
    calls, answer = [], {"status": "invalid"}

    def fake(provider, key, email, url=""):
        calls.append(email)
        if answer.get("raise"):
            raise answer["raise"]
        return {"status": answer["status"], "detail": "mailbox_not_found", "raw": {}}

    monkeypatch.setattr(email_verification, "verify_with", fake)
    return user, calls, answer


def lead_for(db, user, email, website, category="Dentist"):
    from app.models import Lead

    lead = Lead(user_id=user.id, business_name="B", primary_email=email, website_url=website, category=category)
    db.add(lead)
    db.commit()
    return lead


def test_verification_skips_leads_that_are_not_risky(db, verifier):
    from app.services.email_verification import verification_problem

    user, calls, _ = verifier
    lead = lead_for(db, user, "info@smile.com", "https://www.smile.com/")
    assert verification_problem(db, user.id, lead, lead.primary_email) == "" and calls == []


def test_off_domain_address_is_checked_once_and_blocked_when_dead(db, verifier):
    from app.models import Lead, LeadEmail
    from app.services.email_verification import verification_problem

    user, calls, _ = verifier
    lead = lead_for(db, user, "info@lpdentalcosmetic.com", "https://www.lpdentalandcosmetic.com/")
    problem = verification_problem(db, user.id, lead, lead.primary_email)
    assert "doesn't exist" in problem and calls == ["info@lpdentalcosmetic.com"]
    assert db.get(Lead, lead.id).email_verification_status == "invalid"
    stored = db.query(LeadEmail).filter(LeadEmail.lead_id == lead.id).one()
    assert "different company's domain" in stored.verification_details["why_checked"]
    assert verification_problem(db, user.id, lead, lead.primary_email) and len(calls) == 1  # cached, no new check


def test_high_bounce_business_types_are_checked(db, verifier):
    from app.models import EmailMessage
    from app.services.email_verification import verification_problem

    user, calls, answer = verifier
    answer["status"] = "valid"
    account = make_account(db, user, "a@gmail.com")
    for i in range(10):  # plumbers: 2 of 10 bounced in your sending
        old = lead_for(db, user, f"p{i}@plumb{i}.com", f"https://plumb{i}.com", category="Plumber")
        db.add(EmailMessage(user_id=user.id, email_account_id=account.id, lead_id=old.id, recipient_email=old.primary_email,
                            subject="s", status="sent", sent_at=datetime.now(UTC),
                            bounced_at=datetime.now(UTC) if i < 2 else None))
    db.commit()
    lead = lead_for(db, user, "info@newplumber.com", "https://newplumber.com", category="Plumber")
    assert verification_problem(db, user.id, lead, lead.primary_email) == "" and calls == ["info@newplumber.com"]


def test_monthly_limit_and_used_up_allowance_never_stop_outreach(db, verifier):
    from app.models import UserSettings
    from app.services.email_verification import VerifierError, verification_problem

    user, calls, answer = verifier
    for i in range(3):
        verification_problem(db, user.id, lead_for(db, user, f"x{i}@other{i}.com", f"https://site{i}.com"), f"x{i}@other{i}.com")
    assert len(calls) == 3
    # limit of 3 reached: an ordinary address goes out unchecked; another company's address is skipped (unconfirmed)
    assert verification_problem(db, user.id, lead_for(db, user, "info@site.com", "https://site.com"), "info@site.com") == ""
    # a risky type (admin@) is blocked when it can't be confirmed
    assert verification_problem(db, user.id, lead_for(db, user, "admin@site.com", "https://site.com"), "admin@site.com").startswith("Blocked")
    assert verification_problem(
        db, user.id, lead_for(db, user, "y@other.com", "https://site.com"), "y@other.com").startswith("Blocked")
    assert len(calls) == 3
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    settings.email_verifier_monthly_limit, settings.email_verifier_usage = 10, {}
    db.commit()
    answer["raise"] = VerifierError("ZeroBounce: out of credits", quota=True)
    assert verification_problem(db, user.id, lead_for(db, user, "info@s.com", "https://s.com"), "info@s.com") == ""
    # a risky address reaches the service, which says the credits are gone: marked, and the risky address is blocked
    assert verification_problem(db, user.id, lead_for(db, user, "z@other.com", "https://s.com"), "z@other.com").startswith("Blocked")
    db.refresh(settings)
    assert settings.email_verifier_usage["exhausted"] is True


def test_provider_answers_are_mapped(monkeypatch):
    from app.services import email_verification as ev

    class Response:
        def __init__(self, data, code=200):
            self._data, self.status_code, self.content = data, code, b"x"

        def json(self):
            return self._data

    answers = {
        "zb_invalid": Response({"status": "invalid", "sub_status": "mailbox_not_found"}),
        "zb_catch": Response({"status": "catch-all", "sub_status": ""}),
        "hu_invalid": Response({"data": {"status": "invalid", "result": "undeliverable"}}),
        "hu_valid": Response({"data": {"status": "valid", "result": "deliverable"}}),
        "hu_quota": Response({"errors": [{"details": "You have reached your usage limit"}]}, 429),
    }
    current = {}
    monkeypatch.setattr(ev.httpx, "get", lambda *a, **k: answers[current["k"]])
    for k, provider, expected in [("zb_invalid", "zerobounce", "invalid"), ("zb_catch", "zerobounce", "accept_all"),
                                  ("hu_invalid", "hunter", "invalid"), ("hu_valid", "hunter", "valid")]:
        current["k"] = k
        assert ev.verify_with(provider, "key", "a@b.com")["status"] == expected
    current["k"] = "hu_quota"
    with pytest.raises(ev.VerifierError) as err:
        ev.verify_with("hunter", "key", "a@b.com")
    assert err.value.quota


def test_domain_bounce_overview(db):
    from app.models import EmailMessage
    from app.services.health_alerts import domain_overview

    user = make_user(db)
    a = make_account(db, user, "ahad@beecreative.pk")
    b = make_account(db, user, "hashir@beecreative.pk")
    make_account(db, user, "someone@gmail.com")  # free mail: not a domain of yours
    for account, bounces in ((a, 1), (b, 2)):
        for i in range(25):
            db.add(EmailMessage(user_id=user.id, email_account_id=account.id, recipient_email=f"r{i}@x.com", subject="s",
                                status="sent", sent_at=datetime.now(UTC), bounced_at=datetime.now(UTC) if i < bounces else None))
    db.commit()
    [row] = domain_overview(db, user.id)
    assert row["domain"] == "beecreative.pk" and row["sent"] == 50 and row["bounced"] == 3 and abs(row["rate"] - 0.06) < 1e-9


def test_own_checker_end_to_end(monkeypatch):
    """The app talks to apps/verifier over its real API (only the mail server's answer is simulated)."""
    import sys
    from pathlib import Path

    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "verifier"))
    import verifier

    from app.services import email_verification as ev

    monkeypatch.setattr(verifier, "TOKEN", "secret")
    answers = {"dead@acme.com": verifier.Result("invalid", "550 5.1.1 does not exist", "mx.acme.com"),
               "info@acme.com": verifier.Result("valid", "250 OK", "mx.acme.com")}
    monkeypatch.setattr(verifier, "check", lambda email: answers[email])
    client = TestClient(verifier.app)
    monkeypatch.setattr(ev.httpx, "post", lambda url, **k: client.post(url.replace("https://verify.test", ""), json=k["json"], headers=k["headers"]))
    monkeypatch.setattr(ev.httpx, "get", lambda url, **k: client.get(url.replace("https://verify.test", ""), headers=k["headers"]))
    monkeypatch.setattr(verifier.socket, "create_connection", lambda *a, **k: type("C", (), {"close": lambda self: None})())

    assert ev.verify_with("own", "secret", "dead@acme.com", "https://verify.test")["status"] == "invalid"
    assert ev.verify_with("own", "secret", "info@acme.com", "https://verify.test/")["status"] == "valid"
    assert "works" in ev.account_status("own", "secret", "https://verify.test")
    with pytest.raises(ev.VerifierError):
        ev.verify_with("own", "wrong-token", "info@acme.com", "https://verify.test")

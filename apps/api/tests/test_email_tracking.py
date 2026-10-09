import email
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import inbox, tracking
from app.services.gmail import _build_message


def _settings(monkeypatch, **values):
    base = {"secret_key": "test-secret", "public_url": "https://t.example.com", "enable_email_open_tracking": True,
            "frontend_url": "http://localhost:3000"}
    base.update(values)
    monkeypatch.setattr(tracking, "get_settings", lambda: SimpleNamespace(**base))


def test_open_token_round_trip_and_tamper(monkeypatch):
    _settings(monkeypatch)
    token = tracking.open_token("abc-123")
    assert tracking.message_id_from_token(token) == "abc-123"
    assert tracking.message_id_from_token("other-id." + token.split(".")[1]) is None
    assert tracking.message_id_from_token("abc-123") is None


def test_pixel_and_unsubscribe_use_public_url(monkeypatch):
    _settings(monkeypatch)
    assert 'src="https://t.example.com/t/o/' in tracking.open_pixel_html("m1")
    monkeypatch.setattr("app.utils.security.create_unsubscribe_token", lambda u, e: "TOKEN")
    url = tracking.unsubscribe_url("u1", "a@b.com")
    assert url == "https://t.example.com/t/u/TOKEN"
    pixel = tracking.open_pixel_html("m1")
    assert "opacity" not in pixel and "display" not in pixel  # a plain image, no newsletter-style hiding


def test_without_public_url_no_pixel_and_frontend_unsubscribe(monkeypatch):
    _settings(monkeypatch, public_url="")
    assert tracking.open_pixel_html("m1") == ""
    monkeypatch.setattr("app.utils.security.create_unsubscribe_token", lambda u, e: "TOKEN")
    url = tracking.unsubscribe_url("u1", "a@b.com")
    assert url == "http://localhost:3000/unsubscribe/TOKEN"


class FakeDB:
    def __init__(self, message):
        self.message, self.added, self.commits = message, [], 0

    def get(self, _model, _id):
        return self.message

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def test_record_open_ignores_scanner_hits_right_after_send(monkeypatch):
    _settings(monkeypatch)
    msg = SimpleNamespace(id="m1", sent_at=datetime.now(UTC), open_count=0, opened_at=None, last_opened_at=None)
    assert tracking.record_open(FakeDB(msg), "m1") is False
    assert msg.open_count == 0
    msg.sent_at = datetime.now(UTC) - timedelta(minutes=5)
    assert tracking.record_open(FakeDB(msg), "m1") is True
    assert tracking.record_open(FakeDB(msg), "m1") is True
    assert msg.open_count == 2 and msg.opened_at is not None


def test_build_message_sets_custom_headers():
    msg = _build_message(sender="me@x.com", to="a@b.com", subject="Hi", html_body="<p>x</p>",
                         headers={"Message-ID": "<id@x.com>", "List-Unsubscribe": "<https://t/u/1>"})
    parsed = email.message_from_string(msg.as_string())
    assert parsed["Message-ID"] == "<id@x.com>"
    assert parsed["List-Unsubscribe"] == "<https://t/u/1>"


def test_imap_host_and_date_helpers():
    assert inbox.imap_host_for({"host": "smtp.gmail.com"}) == "imap.gmail.com"
    assert inbox.imap_host_for({"host": "smtp.example.org"}) == "imap.example.org"
    assert inbox.imap_host_for({"host": "mailpit"}) == ""
    assert inbox.imap_host_for({"host": "mailpit", "imap_host": "imap.x"}) == "imap.x"
    assert inbox._imap_date(datetime(2026, 10, 7)) == "07-Oct-2026"


# ---------------------------------------------------------------- inbox check with a fake IMAP server

def _raw(headers: dict, body: str = "") -> bytes:
    return ("".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n" + body).encode()


class FakeIMAP:
    def __init__(self, messages: dict[int, bytes]):
        self.messages = messages

    def login(self, *_):
        return "OK", []

    def select(self, *_args, **_kw):
        return "OK", [b"1"]

    def response(self, name):
        return name, [b"42"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(str(u) for u in self.messages).encode()]
        uids = [int(u) for u in args[0].split(",")]
        return "OK", [(f"{u} (UID {u} BODY[] {{1}}".encode(), self.messages[u]) for u in uids if u in self.messages]

    def list(self):
        return "OK", []  # no Sent folder in this fake mailbox

    def logout(self):
        pass


class InboxDB:
    def __init__(self, messages, leads):
        self.messages, self.leads, self.added = messages, leads, []

    def query(self, _model):
        db = self

        class Q:
            def filter(self, *_):
                return self

            def all(self):
                return db.messages

            def __iter__(self):
                return iter(db.messages)

        return Q()

    def get(self, _model, lead_id):
        return self.leads.get(lead_id)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def test_check_account_detects_reply_and_bounce(monkeypatch):
    sent = datetime.now(UTC) - timedelta(days=1)
    reply_target = SimpleNamespace(id="m1", user_id="u1", lead_id="l1", recipient_email="owner@shop.com",
                                   rfc_message_id="<one@me.com>", sent_at=sent, replied_at=None, bounced_at=None,
                                   status="sent", email_account_id="a1")
    bounce_target = SimpleNamespace(id="m2", user_id="u1", lead_id="l2", recipient_email="nobody@gone.com",
                                    rfc_message_id="<two@me.com>", sent_at=sent, replied_at=None, bounced_at=None,
                                    status="sent", email_account_id="a1")
    leads = {"l1": SimpleNamespace(email_status="contacted"), "l2": SimpleNamespace(email_status="contacted")}
    now = email.utils.format_datetime(datetime.now(UTC))
    fake = FakeIMAP({
        5: _raw({"From": "Owner <Owner@Shop.com>", "Date": now, "In-Reply-To": "<one@me.com>",
                 "Message-ID": "<r1@shop.com>", "Subject": "Re: your site", "Content-Type": "text/plain"},
                "Sounds good, can you call me Tuesday?"),
        6: _raw({"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Date": now},
                "Final-Recipient: rfc822; nobody@gone.com\r\nMessage-ID: <two@me.com>"),
        7: _raw({"From": "stranger@else.com", "Date": now}),
    })
    suppressed, saved = [], []
    from app.services import conversations

    monkeypatch.setattr(conversations, "reply_message_ids", lambda db, user_id: set())
    monkeypatch.setattr(conversations, "thread_index", lambda db, roots: {r.rfc_message_id: r for r in roots})
    monkeypatch.setattr(conversations, "add_message", lambda db, root, **kw: saved.append((root.id, kw)) or True)
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr(inbox, "_smtp_payload", lambda a: {"host": "smtp.gmail.com", "username": "me@me.com", "password": "x"})
    monkeypatch.setattr(inbox, "add_suppression", lambda db, uid, addr, **kw: suppressed.append((addr, kw["reason"])))
    account = SimpleNamespace(id="a1", user_id="u1", email_address="me@me.com", inbox_uidvalidity=None,
                              inbox_last_uid=None, inbox_checked_at=None, inbox_error="", sent_folder="",
                              sent_uidvalidity=None, sent_last_uid=None)

    result = inbox.check_account(InboxDB([reply_target, bounce_target], leads), account)

    assert result == {"checked": 3, "replies": 1, "bounces": 1, "opt_outs": 0, "answers_from_gmail": 0}
    assert len(saved) == 1 and saved[0][0] == "m1"  # the reply's text is saved in the conversation
    assert saved[0][1]["direction"] == "in" and "Tuesday" in saved[0][1]["body_text"]
    assert saved[0][1]["rfc_message_id"] == "<r1@shop.com>" and saved[0][1]["subject"] == "Re: your site"
    assert reply_target.replied_at and leads["l1"].email_status == "replied"
    assert bounce_target.bounced_at and bounce_target.status == "bounced" and leads["l2"].email_status == "bounced"
    assert suppressed == [("nobody@gone.com", "bounced")]
    assert account.inbox_uidvalidity == 42 and account.inbox_last_uid == 7


# ---------------------------------------------------------------- click tracking

def _click_settings(monkeypatch, **values):
    base = {"secret_key": "test-secret", "public_url": "https://t.example.com", "enable_email_open_tracking": True,
            "enable_email_click_tracking": True, "frontend_url": "http://localhost:3000"}
    base.update(values)
    monkeypatch.setattr(tracking, "get_settings", lambda: SimpleNamespace(**base))


def test_track_links_rewrites_body_links_but_not_unsubscribe(monkeypatch):
    _click_settings(monkeypatch)
    html = ('<p>See <a href="https://shop.com/a?x=1&amp;y=2">this</a> and <a href=\'http://b.com\'>b</a></p>'
            '<p><a href="https://t.example.com/t/u/TOKEN">Unsubscribe</a> <a href="mailto:me@x.com">mail</a></p>')
    out = tracking.track_links(html, "m1", skip=("https://t.example.com/t/u/TOKEN",))
    links = re.findall(r"href=[\"']([^\"']+)", out)
    assert links[0].startswith("https://t.example.com/t/c/") and links[1].startswith("https://t.example.com/t/c/")
    assert links[2] == "https://t.example.com/t/u/TOKEN" and links[3] == "mailto:me@x.com"
    token = links[0].rsplit("/", 1)[1]
    assert tracking.click_target(token) == ("m1", "https://shop.com/a?x=1&y=2")


def test_click_target_rejects_tampering(monkeypatch):
    _click_settings(monkeypatch)
    token = tracking.click_token("m1", "https://shop.com")
    payload, sig = token.rsplit(".", 1)
    forged = tracking.click_token("m1", "https://evil.com").rsplit(".", 1)[0] + "." + sig
    assert tracking.click_target(forged) is None
    assert tracking.click_target(payload + ".bad") is None
    assert tracking.click_target(tracking.click_token("m1", "javascript:alert(1)")) is None


def test_track_links_off_without_public_url(monkeypatch):
    _click_settings(monkeypatch, public_url="")
    html = '<a href="https://shop.com">x</a>'
    assert tracking.track_links(html, "m1") == html


def test_record_click_counts_and_implies_open(monkeypatch):
    _click_settings(monkeypatch)
    msg = SimpleNamespace(id="m1", sent_at=datetime.now(UTC) - timedelta(minutes=5), click_count=0, clicked_at=None,
                          last_clicked_at=None, opened_at=None)
    db = FakeDB(msg)
    assert tracking.record_click(db, "m1", "https://shop.com") is True
    assert msg.click_count == 1 and msg.clicked_at and msg.opened_at
    assert db.added[0].event_type == "click"


# ---------------------------------------------------------------- lead workflow stage

def _lead(**kw):
    base = {"do_not_contact": False, "website_url": "https://shop.com", "primary_email": "a@shop.com"}
    return SimpleNamespace(**(base | kw))


def _audit(**kw):
    base = {"status": "ready", "sent_at": None, "pdf_size": 1000}
    return SimpleNamespace(**(base | kw))


def _msg(**kw):
    base = {"bounced_at": None, "replied_at": None, "click_count": 0, "open_count": 0}
    return SimpleNamespace(**(base | kw))


def test_stage_for_happy_path_and_side_states():
    from app.services.lead_outreach import stage_for

    now = datetime.now(UTC)
    assert stage_for(_lead(website_url=""), None, None) == "no_website"
    assert stage_for(_lead(primary_email=""), None, None) == "no_email"
    assert stage_for(_lead(do_not_contact=True), None, None) == "do_not_contact"
    assert stage_for(_lead(), None, None) == "website_found"
    assert stage_for(_lead(), _audit(status="capturing"), None) == "auditing"
    assert stage_for(_lead(), _audit(status="generating_pdf"), None) == "audited"
    assert stage_for(_lead(), _audit(status="drafting_email"), None) == "pdf_generated"
    assert stage_for(_lead(), _audit(status="ready"), None) == "email_generated"
    assert stage_for(_lead(), _audit(status="failed"), None) == "failed"
    assert stage_for(_lead(), _audit(status="send_failed"), None) == "send_failed"
    sent = _audit(status="sent", sent_at=now)
    assert stage_for(_lead(), sent, _msg()) == "sent"
    assert stage_for(_lead(), sent, _msg(open_count=2)) == "opened"
    assert stage_for(_lead(), sent, _msg(open_count=2, click_count=1)) == "clicked"
    assert stage_for(_lead(), sent, _msg(open_count=2, replied_at=now)) == "replied"
    assert stage_for(_lead(), sent, _msg(bounced_at=now)) == "bounced"
    # A new draft after sending keeps the lead at its tracking stage
    assert stage_for(_lead(), _audit(status="ready", sent_at=now), _msg(open_count=1)) == "opened"


# ---------------------------------------------------------------- audit campaigns

def test_audit_campaign_lead_mirrors_its_audit():
    from app.services.campaigns import _mirror_audit, is_audit_campaign

    assert is_audit_campaign(SimpleNamespace(extra={"outreach_mode": "audit"}))
    assert not is_audit_campaign(SimpleNamespace(extra={}))

    def cl():
        return SimpleNamespace(status="auditing", sent_at=None, error_message="", subject="", locked_at=1, locked_by="x")

    now = datetime.now(UTC)
    item = cl()
    _mirror_audit(item, SimpleNamespace(step="", status="analyzing", sent_at=None, email_subject="", error_message=""))
    assert item.status == "auditing" and item.locked_at is None
    item = cl()
    _mirror_audit(item, SimpleNamespace(step="", status="sent", sent_at=now, email_subject="Your site", error_message=""))
    assert (item.status, item.sent_at, item.subject) == ("sent", now, "Your site")
    item = cl()
    _mirror_audit(item, SimpleNamespace(step="", status="ready", sent_at=None, email_subject="Draft", error_message=""))
    assert item.status == "ready"
    item = cl()
    _mirror_audit(item, SimpleNamespace(step="", status="failed", sent_at=None, email_subject="", error_message="Site down"))
    assert (item.status, item.error_message) == ("failed", "Site down")
    item = cl()  # a redraft after sending keeps the lead counted as sent
    _mirror_audit(item, SimpleNamespace(step="", status="ready", sent_at=now, email_subject="New", error_message=""))
    assert item.status == "sent"


def test_waiting_for_send_limit_keeps_campaign_lead_open():
    from app.services.campaigns import _mirror_audit
    from app.services.website_audit import WAITING_FOR_LIMIT

    item = SimpleNamespace(status="auditing", sent_at=None, error_message="", subject="", locked_at=None, locked_by="")
    _mirror_audit(item, SimpleNamespace(status="ready", sent_at=None, step=WAITING_FOR_LIMIT, email_subject="Hi", error_message=""))
    assert item.status == "auditing" and "limit" in item.error_message

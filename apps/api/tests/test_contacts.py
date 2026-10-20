from types import SimpleNamespace

from app.services import contacts
from app.services.contacts import classify, extract_contacts, ranked_contacts, score, select_recipients

TEAM_PAGE = """
<html><body>
  <h1>Meet the team</h1>
  <div class="card"><h3>Sarah Mitchell</h3><p>Founder &amp; Managing Director</p>
    <a href="mailto:sarah@acmeplumbing.com">sarah@acmeplumbing.com</a></div>
  <div class="card"><h3>Tom Reyes</h3><p>Marketing Manager</p>
    <a href="mailto:tom.reyes@acmeplumbing.com">tom.reyes@acmeplumbing.com</a></div>
  <footer>
    <a href="mailto:info@acmeplumbing.com">info@acmeplumbing.com</a>
    <a href="mailto:sales@acmeplumbing.com">sales@acmeplumbing.com</a>
    <a href="mailto:careers@acmeplumbing.com">careers@acmeplumbing.com</a>
    Website by <a href="mailto:hello@bestwebagency.com">hello@bestwebagency.com</a>
  </footer>
</body></html>
"""


def test_extracts_named_contacts_and_drops_third_parties():
    found = {c["email"]: c for c in extract_contacts([("https://acmeplumbing.com/team", TEAM_PAGE)], "acmeplumbing.com")}
    assert "hello@bestwebagency.com" not in found  # the web agency is not this business
    assert found["sarah@acmeplumbing.com"]["contact_name"] == "Sarah Mitchell"
    assert "Founder" in found["sarah@acmeplumbing.com"]["job_title"] or "Director" in found["sarah@acmeplumbing.com"]["job_title"]
    assert found["sarah@acmeplumbing.com"]["email_type"] == "personal"
    assert found["info@acmeplumbing.com"]["email_type"] == "generic"
    assert found["sales@acmeplumbing.com"]["email_type"] == "department"


def test_classify_and_score():
    assert classify("info@x.com") == "generic"
    assert classify("hello@x.com") == "generic"
    assert classify("marketing@x.com") == "department"
    assert classify("jane.doe@x.com") == "personal"
    assert score("noreply@x.com", "generic")[0] == 0
    assert score("careers@x.com", "department")[0] == 0
    owner = score("sarah@x.com", "personal", "Founder", "Sarah")[0]
    marketing = score("tom@x.com", "personal", "Marketing Manager", "Tom")[0]
    sales = score("sales@x.com", "department")[0]
    generic = score("info@x.com", "generic")[0]
    unknown_person = score("jane@x.com", "personal")[0]
    # an address that only looks like a name now ranks below info@ (it bounced 3x more in real sending)
    assert owner > marketing > sales > generic > unknown_person > 0
    # a personal-looking address with no stated role is not ranked above a named decision-maker or department
    assert unknown_person < sales


def _lead(rows, override=""):
    return SimpleNamespace(
        user_id="u", primary_email=rows[0].email if rows else "", emails=rows, extra={"primary_override": override} if override else {}
    )


def _row(email, kind, name="", title="", status="domain_ok"):
    return SimpleNamespace(email=email, email_type=kind, contact_name=name, job_title=title, verification_status=status)


def test_primary_and_cc_selection(monkeypatch):
    monkeypatch.setattr(contacts, "refresh_verification", lambda db, lead: None)
    suppressed = {"tom@acme.com"}
    monkeypatch.setattr("app.services.suppression.is_suppressed", lambda db, user, email: email in suppressed)
    rows = [
        _row("info@acme.com", "generic"),
        _row("sarah@acme.com", "personal", "Sarah", "Owner"),
        _row("tom@acme.com", "personal", "Tom", "Marketing Manager"),
        _row("sales@acme.com", "department"),
        _row("old@acme.com", "personal", status="bounced"),
    ]
    lead = _lead(rows)
    plain = select_recipients(None, lead)
    assert plain["to"] == "sarah@acme.com" and plain["cc"] == []  # CC is off unless the campaign turns it on
    cc = select_recipients(None, lead, cc_enabled=True, cc_max=2)["cc"]
    assert "sales@acme.com" in cc
    assert "sarah@acme.com" not in cc  # never the primary
    assert "tom@acme.com" not in cc  # never a suppressed address
    assert "info@acme.com" not in cc and "old@acme.com" not in cc  # never general inboxes or bounced addresses
    # your choice wins
    assert select_recipients(None, _lead(rows, override="sales@acme.com"))["to"] == "sales@acme.com"


def test_generic_inbox_is_kept_as_fallback(monkeypatch):
    monkeypatch.setattr(contacts, "refresh_verification", lambda db, lead: None)
    monkeypatch.setattr("app.services.suppression.is_suppressed", lambda db, user, email: False)
    only_generic = _lead([_row("info@shop.com", "generic")])
    assert select_recipients(None, only_generic)["to"] == "info@shop.com"  # the business is not dropped
    excluded = select_recipients(None, only_generic, include_generic=False)
    assert excluded["to"] == "" and "general inboxes excluded" in excluded["reason"]


def test_ranking_excludes_ineligible():
    lead = _lead([_row("a@x.com", "personal", status="domain_rejects"), _row("info@x.com", "generic")])
    assert [r.email for r, _, _ in ranked_contacts(lead)] == ["info@x.com"]

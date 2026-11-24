"""The Website audits page splits audits into two tabs: "Audits" (sent or on their way) and "Not sent" (failed audits
and emails that didn't go out). Real PostgreSQL, nothing is sent."""

from datetime import UTC, datetime

from app.services.campaigns import CAMPAIGN_STOPPED_STEP
from app.services.website_audit import count_audits, list_audits

from test_send_protection import db, make_user, pg  # noqa: F401 (fixtures: real PostgreSQL)


def test_not_sent_audits_have_their_own_tab(db):
    from app.models import Lead, WebsiteAudit

    user = make_user(db)
    lead = Lead(user_id=user.id, business_name="X", primary_email="x@x.com")
    db.add(lead)
    db.flush()
    rows = {
        "sent": dict(status="sent", step="Email sent", sent_at=datetime.now(UTC)),
        "review": dict(status="ready", step="Ready to review and send"),
        "running": dict(status="analyzing", step="Analysing"),
        "cancelled": dict(status="cancelled", step="Stopped by you"),
        "failed": dict(status="failed", step="Audit failed"),
        "send_failed": dict(status="send_failed", step="Sending failed"),
        "stopped": dict(status="ready", step=CAMPAIGN_STOPPED_STEP),
        "bounce": dict(status="ready", step="Not sent: the address may bounce (choose another on the lead)"),
        "held": dict(status="ready", step="Held: the local risk check couldn't clear this address (choose another on the lead)"),
    }
    for name, fields in rows.items():
        db.add(WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url=f"https://{name}.com", **fields))
    db.commit()

    def sites(view):
        return {a.website_url.removeprefix("https://").removesuffix(".com") for a in list_audits(db, user.id, view=view, limit=50)}

    assert sites("main") == {"sent", "review", "running", "cancelled"}
    assert sites("not_sent") == {"failed", "send_failed", "stopped", "bounce", "held"}
    assert len(list_audits(db, user.id, limit=50)) == len(rows)  # no view: everything (lead pages, older callers)
    assert count_audits(db, user.id, view="main") == 4 and count_audits(db, user.id, view="not_sent") == 5
    assert count_audits(db, user.id, q="bounce", view="not_sent") == 1  # search works inside a tab

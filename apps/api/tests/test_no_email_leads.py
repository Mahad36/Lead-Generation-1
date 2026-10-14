from types import SimpleNamespace

from app.services.email_recheck import discard_still_missing


class FakeQuery:
    def __init__(self, hit):
        self.hit = hit

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.hit


class FakeDb:
    def __init__(self, in_campaign=()):
        self.deleted, self.in_campaign = [], set(in_campaign)
        self._current = None

    def query(self, *a):
        return FakeQuery(self._current in self.in_campaign or None)

    def delete(self, lead):
        self.deleted.append(lead.id)

    def commit(self):
        pass


def lead(id_, email="", status="no_email", contacted=None):
    return SimpleNamespace(id=id_, primary_email=email, status=status, last_contacted_at=contacted)


def test_only_leads_still_without_email_are_discarded():
    db = FakeDb()
    leads = [lead("a"), lead("b", email="x@y.com", status="ready"), lead("c", contacted="2026-10-01"), lead("d", status="ready")]
    original = db.query

    def query(*a):
        db._current = leads_iter.current
        return original(*a)

    class Iter:
        current = None

    leads_iter = Iter()
    db.query = query
    removed = 0
    for item in leads:
        leads_iter.current = item.id
        removed += discard_still_missing(db, [item])
    assert db.deleted == ["a"] and removed == 1  # never a lead with an email, a contacted one, or a finished one


def test_a_lead_in_a_campaign_is_kept():
    db = FakeDb(in_campaign={"a"})
    db._current = "a"
    assert discard_still_missing(db, [lead("a")]) == 0 and db.deleted == []

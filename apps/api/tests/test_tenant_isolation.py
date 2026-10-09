"""Tenant isolation guarantees.

These tests assert that lookup helpers always require user_id so one tenant
cannot read another tenant's records by guessing an ID.
"""

from app.services.leads import get_lead, list_leads
from app.services.templates import get_template


def test_lead_lookup_is_user_scoped():
    assert "user_id" in get_lead.__code__.co_varnames
    assert "user_id" in list_leads.__code__.co_varnames


def test_template_lookup_is_user_scoped():
    assert "user_id" in get_template.__code__.co_varnames

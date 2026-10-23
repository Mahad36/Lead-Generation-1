import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
ROOT = API_ROOT.parents[1]
for path in (str(API_ROOT), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


import pytest


def fake_dns(name: str, rtype: str) -> dict:
    """Every domain publishes a mail server that has an address (tests that need other answers patch their own)."""
    if rtype == "MX":
        return {"Status": 0, "Answer": [{"type": 15, "data": f"10 mx.{name}."}]}
    if rtype == "A":
        return {"Status": 0, "Answer": [{"type": 1, "data": "192.0.2.10"}]}
    return {"Status": 0, "Answer": []}


@pytest.fixture(autouse=True)
def _no_network_dns(monkeypatch):
    """No test makes real DNS lookups (the local address risk check would otherwise ask public resolvers)."""
    from app.services import deliverability

    monkeypatch.setattr(deliverability, "dns_query", fake_dns)

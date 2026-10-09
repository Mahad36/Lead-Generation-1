from types import SimpleNamespace

from app.services import tracking
from app.services.portfolio import csv_export_url, keep_known, parse_sheet
from app.services.website_audit import _portfolio_links

COLUMN_SHEET = (
    "Skin Care/ Beauty,Personal Brand,Sports\n"
    "https://rikelleskin.com/,https://nydrconnect.com/ (SquareSpace),\n"
    "koreancosmetics.nl ,https://www.brandiilou.co/ (Wix),\n"
    "rikelleskin.com,,\n"
)


def test_column_per_niche_sheet():
    sites = parse_sheet(COLUMN_SHEET)
    assert [(s["domain"], s["niche"], s["platform"]) for s in sites] == [
        ("rikelleskin.com", "Skin Care/ Beauty", ""),
        ("koreancosmetics.nl", "Skin Care/ Beauty", ""),
        ("nydrconnect.com", "Personal Brand", "SquareSpace"),
        ("brandiilou.co", "Personal Brand", "Wix"),
    ]  # duplicate removed, notes become the platform, the empty column is skipped
    assert sites[2]["url"] == "https://nydrconnect.com"


def test_row_per_site_sheet():
    sites = parse_sheet("Client,Website URL,Niche\nAcme,acme.com,Plumbing\nBeta,https://beta.io/,Law Firms\n")
    assert [(s["domain"], s["niche"]) for s in sites] == [("acme.com", "Plumbing"), ("beta.io", "Law Firms")]


def test_share_link_becomes_csv_export():
    url = "https://docs.google.com/spreadsheets/d/1-BfETaOf/edit?usp=sharing#gid=42"
    assert csv_export_url(url) == "https://docs.google.com/spreadsheets/d/1-BfETaOf/export?format=csv&gid=42"


def test_only_sheet_sites_survive():
    portfolio = {"Skin Care/ Beauty": ["https://rikelleskin.com"], "Personal Brand": ["https://nydrconnect.com (SquareSpace)"]}
    kept = keep_known(
        [{"url": "www.rikelleskin.com/", "note": "skincare"}, {"url": "https://invented.example", "note": "x"},
         {"url": "https://nydrconnect.com", "note": "brand"}],
        portfolio,
    )
    assert [(k["url"], k["niche"]) for k in kept] == [
        ("https://rikelleskin.com", "Skin Care/ Beauty"),
        ("https://nydrconnect.com", "Personal Brand"),
    ]


def test_portfolio_link_is_never_click_tracked(monkeypatch):
    monkeypatch.setattr(tracking, "get_settings", lambda: SimpleNamespace(
        secret_key="k", public_url="https://t.example.com", enable_email_click_tracking=True))
    audit = SimpleNamespace(report={"reference_sites": [{"url": "https://rikelleskin.com"}]})
    html = '<p>We built <a href="https://rikelleskin.com">https://rikelleskin.com</a> and <a href="https://other.com">x</a></p>'
    out = tracking.track_links(html, "m1", skip=("https://t.example.com/t/u/T", *_portfolio_links(audit)))
    assert 'href="https://rikelleskin.com"' in out  # left as the real URL
    assert "https://t.example.com/t/c/" in out  # other links are still tracked

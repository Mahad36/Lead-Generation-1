import base64
import email
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from app.services import performance
from app.services.ai import AIProviderError, AnthropicProvider
from app.services.audit_ai import (
    AUDIT_SCHEMA,
    generate_audit_email,
    generate_audit_report,
    normalize_report,
    parse_json_text,
)
from app.services.audit_pdf import build_audit_pdf, page_weight_rows, pdf_filename, technical_checks
from app.services.gmail import EmailAttachment, build_mime
from app.services.integrations import AIConfig
from app.services.website_audit import _html_to_text, get_audit, list_audits

REPORT = {
    "overall_score": 61,
    "headline": "Fast on desktop, slow on mobile",
    "executive_summary": "Mobile LCP is 4.8 s & the <head> has 3 blocking scripts ≥ budget.",
    "category_scores": [{"category": "Performance", "score": 48, "summary": "Mobile is slow"}],
    "strengths": ["HTTPS is enabled"],
    "findings": [
        {
            "title": "Slow mobile loading",
            "category": "Performance",
            "severity": "high",
            "evidence": "LCP 4.8 s on mobile",
            "impact": "Visitors leave",
            "recommendation": "Compress hero image",
        }
    ],
    "action_plan": [{"priority": 1, "action": "Compress images", "expected_impact": "-1.5 s LCP", "effort": "low"}],
    "conclusion": "Fixing images first gives the biggest win.",
}


class FakeProvider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def generate(self, prompt, *, system, temperature=None, max_tokens=None, json_schema=None, timeout=None, images=None):
        self.calls.append({"prompt": prompt, "json_schema": json_schema, "max_tokens": max_tokens, "images": images})
        return json.dumps(self.payload)


def test_build_mime_attaches_pdf():
    raw = build_mime(
        sender="me@example.com",
        to="owner@example.com",
        subject="Your audit",
        html_body="<p>Hello</p>",
        text_body="Hello",
        attachments=[EmailAttachment(filename="audit.pdf", content=b"%PDF-1.4 test")],
    )
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message.get_content_type() == "multipart/mixed"
    parts = {part.get_content_type(): part for part in message.walk()}
    assert "text/html" in parts and "text/plain" in parts
    pdf = parts["application/pdf"]
    assert pdf.get_filename() == "audit.pdf"
    assert pdf.get_payload(decode=True) == b"%PDF-1.4 test"


def test_build_mime_without_attachments_is_unchanged():
    raw = build_mime(sender="a@x.com", to="b@x.com", subject="s", html_body="<p>x</p>")
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message.get_content_type() == "text/html"


def test_analyze_html_page_checks():
    html = """<html lang="en"><head><title>ABC Plumbing Miami</title>
    <meta name="viewport" content="width=device-width"><script src="/a.js"></script>
    <script src="/b.js" defer></script></head><body><h1>Hi</h1><img src="a.jpg"><img src="b.jpg" alt="b">
    <img src="http://cdn.example/c.jpg" alt="c" loading="lazy"></body></html>"""
    page = performance.analyze_html(html, "https://abc.example/")
    assert page["has_viewport"] and page["has_lang"]
    assert page["render_blocking_scripts"] == 1
    assert page["images_missing_alt"] == 1
    assert page["mixed_content_resources"] == 1
    assert page["h1_count"] == 1


SITE_HTML = """<html lang="en"><head><title>ABC Plumbing</title>
<link rel="stylesheet" href="/css/site.css"><script src="/js/app.js"></script>
<script src="https://tags.example-analytics.com/t.js" async></script>
<script>""" + ("var x = 1;" * 100) + """</script></head>
<body><h1>ABC</h1><img src="/img/hero.jpg"><img src="data:image/png;base64,AAAA"><img src="/img/missing.png"></body></html>"""


@respx.mock
def test_measure_performance_inventories_every_file():
    respx.get("https://abc.example/").mock(return_value=httpx.Response(200, text=SITE_HTML, headers={"content-type": "text/html"}))
    respx.get("https://abc.example/css/site.css").mock(
        return_value=httpx.Response(200, content=b"x" * 5000, headers={"cache-control": "max-age=86400", "content-type": "text/css"})
    )
    respx.get("https://abc.example/js/app.js").mock(return_value=httpx.Response(200, content=b"y" * 9000))
    respx.get("https://tags.example-analytics.com/t.js").mock(return_value=httpx.Response(200, content=b"z" * 100))
    respx.get("https://abc.example/img/hero.jpg").mock(
        return_value=httpx.Response(200, content=b"j" * 300_000, headers={"content-type": "image/jpeg"})
    )
    respx.get("https://abc.example/img/missing.png").mock(return_value=httpx.Response(404))

    result = performance.measure_performance("abc.example")
    res = result["resources"]
    assert result["url"] == "https://abc.example"
    assert result["resources_checked"] == 5  # data: URI skipped
    assert res["total_requests"] == 6
    assert res["by_type"]["script"] == {"count": 2, "bytes": 9100}
    assert res["third_party_requests"] == 1 and res["third_party_domains"] == ["example-analytics.com"]
    assert sorted(res["uncompressed_text_files"]) == ["https://abc.example/css/site.css", "https://abc.example/js/app.js"]
    assert "https://abc.example/css/site.css" not in res["short_or_no_cache"]
    assert res["large_images"][0]["bytes"] == 300_000
    assert res["legacy_format_images"] == ["https://abc.example/img/hero.jpg"]
    assert res["failed"][0]["status"] == 404
    assert res["largest"][0]["url"].endswith("hero.jpg")
    # Claude gets the source with long inline scripts elided
    assert "<title>ABC Plumbing</title>" in result["html_excerpt"]
    assert "inline script: 1000 chars elided" in result["html_excerpt"]


@respx.mock
def test_measure_performance_fails_when_site_unreachable():
    respx.get("https://down.example/").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(performance.PerformanceError):
        performance.measure_performance("https://down.example")


@respx.mock
def test_measure_performance_fails_on_http_error_page():
    respx.get("https://gone.example/").mock(return_value=httpx.Response(503))
    with pytest.raises(performance.PerformanceError, match="503"):
        performance.measure_performance("https://gone.example")


def test_parse_json_text_handles_fences_and_prose():
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('Here you go: {"a": 2} thanks') == {"a": 2}
    with pytest.raises(AIProviderError):
        parse_json_text("no json here")


def test_normalize_report_clamps_and_sorts():
    data = dict(REPORT)
    data["overall_score"] = 140
    data["findings"] = [
        {**REPORT["findings"][0], "title": "Minor", "severity": "low"},
        {**REPORT["findings"][0], "title": "Bad", "severity": "CRITICAL"},
        {**REPORT["findings"][0], "title": "Odd", "severity": "weird"},
    ]
    report = normalize_report(data)
    assert report["overall_score"] == 100
    assert [f["title"] for f in report["findings"]] == ["Bad", "Odd", "Minor"]
    assert report["findings"][1]["severity"] == "medium"
    with pytest.raises(AIProviderError):
        normalize_report({**REPORT, "findings": []})


def test_generate_audit_report_requests_structured_output():
    provider = FakeProvider(REPORT)
    report = generate_audit_report(
        provider,
        lead={"business_name": "ABC"},
        site={"title": "ABC"},
        performance={"probe": {"ttfb_ms": 100}},
        timeout=30,
    )
    assert report["overall_score"] == 61
    assert provider.calls[0]["json_schema"] is AUDIT_SCHEMA
    assert '"ttfb_ms": 100' in provider.calls[0]["prompt"]


def test_generate_audit_email_wraps_plain_text_body():
    provider = FakeProvider({"subject": "", "body_html": "Hi there\n\nThe audit is attached."})
    result = generate_audit_email(
        provider, lead={"business_name": "ABC Plumbing"}, report=REPORT, sender={"sender_name": "Alex"}, timeout=30
    )
    assert result["subject"] == "Website audit for ABC Plumbing"
    assert result["html"] == "<p>Hi there</p><p>The audit is attached.</p>"


def test_build_audit_pdf_renders_with_and_without_resources():
    performance_data = {
        "url": "https://abc.example",
        "probe": {"https": True, "ttfb_ms": 300, "load_ms": 900, "page": {"h1_count": 1}},
        "resources": {
            "total_requests": 6,
            "total_bytes": 420_000,
            "html_bytes": 9_000,
            "by_type": {"script": {"count": 2, "bytes": 90_000}, "image": {"count": 3, "bytes": 320_000}},
            "third_party_requests": 1,
            "third_party_domains": ["tags.example-analytics.com"],
            "largest": [{"url": "https://abc.example/img/hero.jpg?v=2", "type": "image", "bytes": 300_000}],
            "large_images": [{"url": "https://abc.example/img/hero.jpg", "bytes": 300_000}],
        },
    }
    kwargs = dict(
        report=normalize_report(REPORT),
        lead={"business_name": "ABC Plumbing & Sons <Miami>"},
        sender={"sender_name": "Alex", "company_name": "Studio", "contact_email": "alex@studio.test"},
        generated_at=datetime(2026, 10, 6, tzinfo=UTC),
        ai_model="claude-opus-5-5",
    )
    pdf = build_audit_pdf(performance=performance_data, **kwargs)
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000
    pdf_minimal = build_audit_pdf(performance={"url": "https://abc.example"}, **kwargs)
    assert pdf_minimal.startswith(b"%PDF")


def test_page_weight_rows():
    rows = page_weight_rows(
        {"html_bytes": 2048, "total_requests": 3, "total_bytes": 3 * 1024 * 1024, "by_type": {"image": {"count": 2, "bytes": 3_000_000}}}
    )
    assert rows[0] == ("HTML document", "1", "2 KB")
    assert rows[1] == ("Images", "2", "2.9 MB")
    assert rows[-1] == ("Total", "3", "3.0 MB")


def test_technical_checks_flags_problems():
    checks = {
        label: ok
        for label, _value, ok in technical_checks(
            {"https": False, "ttfb_ms": 2000, "page": {}},
            {"total_bytes": 5 * 1024 * 1024, "uncompressed_text_files": ["a.js"], "short_or_no_cache": []},
        )
    }
    assert checks["Secure connection (HTTPS)"] is False
    assert checks["Server response time (TTFB)"] is False
    assert checks["Total page weight"] is False
    assert checks["Scripts/CSS sent uncompressed"] is False
    assert checks["Files with no or short caching"] is True


def test_pdf_filename_is_safe():
    assert pdf_filename("ABC Plumbing & Sons!", datetime(2026, 10, 6)) == "website-audit-abc-plumbing-sons-2026-10-06.pdf"


def test_html_to_text():
    assert _html_to_text("<p>Hello <b>Sam</b></p><p>Bye</p>") == "Hello Sam\n\nBye"


def _anthropic_provider(model: str, response) -> tuple[AnthropicProvider, MagicMock]:
    provider = AnthropicProvider(
        AIConfig(
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
            provider="anthropic",
            model=model,
            temperature=0.4,
            timeout_seconds=30,
        )
    )
    client = MagicMock()
    client.with_options.return_value = client
    client.messages.create.return_value = response
    client.beta.messages.create.return_value = response
    provider.client = client
    return provider, client


def _response(stop_reason="end_turn", text='{"ok": true}'):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        _request_id="req_test",
    )


def test_anthropic_provider_uses_structured_output_and_no_temperature():
    provider, client = _anthropic_provider("claude-opus-5-5", _response())
    text = provider.generate("p", system="s", temperature=0.2, max_tokens=8000, json_schema={"type": "object"})
    assert text == '{"ok": true}'
    params = client.beta.messages.create.call_args.kwargs
    assert "temperature" not in params
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert params["fallbacks"] == "default"
    assert params["max_tokens"] == 8000


def test_anthropic_provider_older_model_uses_plain_endpoint():
    provider, client = _anthropic_provider("claude-haiku-4-5", _response())
    provider.generate("p", system="s")
    params = client.messages.create.call_args.kwargs
    assert "output_config" not in params and "fallbacks" not in params


def test_anthropic_provider_raises_on_refusal():
    provider, _client = _anthropic_provider("claude-opus-5-5", _response(stop_reason="refusal"))
    with pytest.raises(AIProviderError):
        provider.generate("p", system="s")


def test_audit_lookups_are_user_scoped():
    assert "user_id" in get_audit.__code__.co_varnames
    assert "user_id" in list_audits.__code__.co_varnames

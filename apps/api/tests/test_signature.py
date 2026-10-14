from types import SimpleNamespace

from app.services.gmail_signature import account_signature, extract_signature
from app.utils.signature import append_signature, drop_ai_sign_off, ensure_sign_off, insert_signature

GMAIL_HTML = (
    '<div dir="ltr"><p>Hi there,</p><p>Thanks!</p>'
    '<br clear="all"><div><br></div><span class="gmail_signature_prefix">-- </span><br>'
    '<div dir="ltr" class="gmail_signature" data-smartmail="gmail_signature">'
    '<div><b>Syed Ahad Ali</b> | CEO</div><div onclick="evil()">ahad@beecreative.pk</div>'
    '<img src="https://res.cloudinary.com/logo.png"><script>alert(1)</script></div></div>'
)


def test_extract_gmail_signature():
    html, text = extract_signature(GMAIL_HTML)
    assert 'data-smartmail="gmail_signature"' in html
    assert "logo.png" in html  # images kept
    assert "<script" not in html and "onclick" not in html  # nothing executable
    assert text.splitlines()[0].startswith("Syed Ahad Ali")
    assert "Hi there" not in html  # only the signature block
    assert extract_signature("<p>No signature here</p>") == ("", "")


def test_each_account_uses_its_own_signature():
    a = SimpleNamespace(gmail_signature_html="<div>SIG A</div>", gmail_signature_text="SIG A")
    b = SimpleNamespace(gmail_signature_html="<div>SIG B</div>", gmail_signature_text="SIG B")
    none = SimpleNamespace(gmail_signature_html="", gmail_signature_text="")
    assert "SIG A" in account_signature(a)[0] and "SIG B" not in account_signature(a)[0]
    assert "SIG B" in account_signature(b)[0]
    assert account_signature(none) == ("", "")
    assert account_signature(None) == ("", "")


def test_append_signature():
    html, text = append_signature("<p>Hi</p>", "Hi", "<div>SIG</div>", "SIG")
    assert html == "<p>Hi</p><div>SIG</div>" and text == "Hi\n\nSIG"
    assert append_signature("<p>Hi</p>", "Hi", "", "") == ("<p>Hi</p>", "Hi")


def test_ensure_sign_off_when_no_signature():
    assert ensure_sign_off("<p>Q?</p><p>Best,</p>", "Mahad", "Bee Creative") == "<p>Q?</p><p>Best,<br>Mahad<br>Bee Creative</p>"
    body = "<p>Q?</p><p>Best,<br>Mahad</p>"
    assert ensure_sign_off(body, "Mahad", "Bee Creative") == body  # already signed
    assert ensure_sign_off("<p>Want me to put it together?</p>", "Mahad", "") == "<p>Want me to put it together?</p>"


def test_drop_ai_sign_off():
    body = "<p>Want me to put it together?</p><p>Best,<br>Mahad<br>Bee Creatives</p>"
    assert drop_ai_sign_off(body, "Mahad", "Bee Creatives") == "<p>Want me to put it together?</p><p>Best,</p>"
    assert drop_ai_sign_off("<p>Q?</p><p>Mahad, Bee Creatives</p>", "Mahad", "Bee Creatives") == "<p>Q?</p>"
    assert drop_ai_sign_off("<p>Q?</p><p>Best, Mahad</p>", "Mahad", "Bee Creatives") == "<p>Q?</p><p>Best,</p>"
    body = "<p>Hi</p><p>Want me to put it together?</p>"
    assert drop_ai_sign_off(body, "Mahad", "Bee Creatives") == body


def test_insert_signature_above_unsubscribe_and_replaces_name_lines():
    sig = "<div>SIG</div>"
    body = (
        "<p>Hi ABC,</p><p>Would you be open to a short call?</p><p>Mahad</p><p>Bee Creatives</p>"
        '<p style="font-size:12px;color:#666"><a href="https://x/unsub">Unsubscribe</a></p>'
    )
    out = insert_signature(body, sig, "Mahad", "Bee Creatives")
    assert out == (
        "<p>Hi ABC,</p><p>Would you be open to a short call?</p>" + sig
        + '<p style="font-size:12px;color:#666"><a href="https://x/unsub">Unsubscribe</a></p>'
    )
    assert insert_signature("<p>Hi</p>", "", "Mahad", "") == "<p>Hi</p>"


def test_images_that_do_not_load_are_left_out():
    from app.services.gmail_signature import drop_broken_images

    html, removed = drop_broken_images(
        '<div class="gmail_signature" data-smartmail="gmail_signature"><img alt="Logo" src=""><b>Ahad</b></div>'
    )
    assert removed == ["Logo"] and "<img" not in html and "Ahad" in html

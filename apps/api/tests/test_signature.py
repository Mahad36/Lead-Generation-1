from app.utils.signature import append_signature, drop_ai_sign_off, signature_html, signature_text

SIG = "Mahad\nFounder, Bee Creatives\nbeecreatives.com\nhello@beecreatives.com"


def test_signature_html_links_and_lines():
    html = signature_html(SIG)
    assert html.startswith("<p>Mahad<br>Founder, Bee Creatives<br>")
    assert '<a href="https://beecreatives.com">beecreatives.com</a>' in html
    assert '<a href="mailto:hello@beecreatives.com">hello@beecreatives.com</a>' in html
    assert signature_text(SIG) == SIG
    assert signature_html("   ") == "" and signature_html(None) == ""


def test_signature_is_escaped_and_delimiter_dropped():
    html = signature_html("--\nA <b>& B</b>")
    assert html == "<p>A &lt;b&gt;&amp; B&lt;/b&gt;</p>"


def test_append_signature():
    html, text = append_signature("<p>Hi</p>", "Hi", SIG)
    assert html.startswith("<p>Hi</p><p>Mahad<br>")
    assert text == "Hi\n\n" + SIG
    assert append_signature("<p>Hi</p>", "Hi", "") == ("<p>Hi</p>", "Hi")


def test_drop_ai_sign_off():
    body = "<p>Want me to put it together?</p><p>Best,<br>Mahad<br>Bee Creatives</p>"
    assert drop_ai_sign_off(body, "Mahad", "Bee Creatives") == "<p>Want me to put it together?</p><p>Best,</p>"
    assert drop_ai_sign_off("<p>Q?</p><p>Mahad, Bee Creatives</p>", "Mahad", "Bee Creatives") == "<p>Q?</p>"
    assert drop_ai_sign_off("<p>Q?</p><p>Best, Mahad</p>", "Mahad", "Bee Creatives") == "<p>Q?</p><p>Best,</p>"
    # A normal last paragraph is left alone
    body = "<p>Hi</p><p>Want me to put it together?</p>"
    assert drop_ai_sign_off(body, "Mahad", "Bee Creatives") == body


def test_insert_signature_above_unsubscribe_and_replaces_name_lines():
    from app.utils.signature import insert_signature

    sig = signature_html(SIG)
    body = (
        "<p>Hi ABC,</p><p>Would you be open to a short call?</p><p>Mahad</p><p>Bee Creatives</p>"
        '<p style="font-size:12px;color:#666"><a href="{{unsubscribe_url}}">Unsubscribe</a></p>'
    )
    out = insert_signature(body, sig, "Mahad", "Bee Creatives")
    assert out == (
        "<p>Hi ABC,</p><p>Would you be open to a short call?</p>" + sig
        + '<p style="font-size:12px;color:#666"><a href="{{unsubscribe_url}}">Unsubscribe</a></p>'
    )
    assert insert_signature("<p>Hi</p>", sig, "Mahad", "") == "<p>Hi</p>" + sig
    assert insert_signature("<p>Hi</p>", "", "Mahad", "") == "<p>Hi</p>"

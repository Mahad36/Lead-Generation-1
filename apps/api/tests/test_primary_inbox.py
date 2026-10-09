import email
from types import SimpleNamespace

from app.services.gmail import EmailAttachment, _build_message, sender_header
from app.services.inbox import OPT_OUT_RE, _reply_text


def test_untracked_email_keeps_its_html_but_has_no_bulk_headers():
    html = '<p>Hi team,</p><p>I noticed...</p><p style="font-size:12px;color:#666">Not interested? <a href="u">Unsubscribe</a></p>'
    msg = _build_message(
        sender="Mahad <me@gmail.com>",
        to="owner@shop.com",
        subject="The 7pm headline on shop.com",
        html_body=html,
        text_body="Hi team,\n\nI noticed...\n\nUnsubscribe: u",
        attachments=[EmailAttachment(filename="audit.pdf", content=b"%PDF-1.4")],
        headers={"Message-ID": "<x@gmail.com>"},
    )
    parsed = email.message_from_string(msg.as_string())
    html_part = next(p for p in parsed.walk() if p.get_content_type() == "text/html")
    assert "Unsubscribe" in html_part.get_payload(decode=True).decode()  # the visible email is unchanged
    assert "<img" not in html_part.get_payload(decode=True).decode()  # no tracking image
    assert parsed["List-Unsubscribe"] is None and parsed["Message-ID"] == "<x@gmail.com>"
    assert parsed["From"] == "Mahad <me@gmail.com>"


def test_sender_header_uses_a_real_name_only():
    assert sender_header(SimpleNamespace(display_name="Mahad", email_address="me@gmail.com")) == "Mahad <me@gmail.com>"
    assert sender_header(SimpleNamespace(display_name="me@gmail.com", email_address="me@gmail.com")) == "me@gmail.com"
    assert sender_header(SimpleNamespace(display_name="", email_address="me@gmail.com")) == "me@gmail.com"


def test_reply_opt_out_detection_ignores_the_quoted_original():
    raw = (
        "From: owner@shop.com\r\nSubject: Re: audit\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "Thanks for the audit, can we talk Tuesday?\r\n\r\nOn Mon, Mahad wrote:\r\n"
        "> Not interested? Unsubscribe\r\n"
    ).encode()
    text = _reply_text(raw)
    assert "Tuesday" in text and not OPT_OUT_RE.search(text)
    for reply in ["Please remove me from your list", "Not interested, thanks", "unsubscribe", "Stop emailing me",
                  "Don't email me again", "No thanks"]:
        assert OPT_OUT_RE.search(reply), reply
    assert not OPT_OUT_RE.search("Interesting, what would the redesign cost?")

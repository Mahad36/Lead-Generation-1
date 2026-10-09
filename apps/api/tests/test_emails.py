from app.services.email_extraction import extract_emails_from_html
from app.utils.emails import choose_primary_email, extract_emails_from_text, is_plausible_email


def test_extracts_mailto_and_text_emails():
    html = """
    <html><body>
      <a href="mailto:info@abcplumbing.test">Email us</a>
      <p>Sales: sales@abcplumbing.test</p>
      <img src="user@example.com" />
    </body></html>
    """
    emails = extract_emails_from_html(html, "https://abcplumbing.test/contact", "abcplumbing.test")
    values = {item["email"] for item in emails}
    assert "info@abcplumbing.test" in values
    assert "sales@abcplumbing.test" in values
    assert "user@example.com" not in values
    primary = next(item for item in emails if item["is_primary"])
    assert primary["email"] == "info@abcplumbing.test"


def test_false_positives_removed():
    text = "Contact user@domain.com or hello@realbiz.test also noreply@realbiz.test"
    found = extract_emails_from_text(text)
    assert "hello@realbiz.test" in found
    assert "user@domain.com" not in found


def test_primary_prefers_generic_local_part():
    assert choose_primary_email(
        ["owner@abc.test", "info@abc.test", "sales@abc.test"], "abc.test"
    ) == "info@abc.test"


def test_plausible_email():
    assert is_plausible_email("team@shop.test")
    assert not is_plausible_email("not-an-email")
    assert not is_plausible_email("file.png@cdn.googleapis.com")

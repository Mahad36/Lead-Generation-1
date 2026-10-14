from types import SimpleNamespace

from app.utils.greeting import apply_greeting, apply_greeting_text, clean_first_name, owner_first_name


def lead(contact="", business="ABC Plumbing", emails=(), primary=""):
    return SimpleNamespace(contact_name=contact, business_name=business, emails=list(emails), primary_email=primary)


def row(email, name="", title=""):
    return SimpleNamespace(email=email, contact_name=name, job_title=title)


def test_owner_name_or_hi_there():
    assert owner_first_name(lead("Sarah Mitchell")) == "Sarah"
    assert owner_first_name(lead("")) == ""  # -> "Hi there,"
    assert owner_first_name(lead("ABC Plumbing")) == ""  # the business name is not a person
    assert owner_first_name(lead("info")) == ""
    # the person it goes to wins, then an owner named on the website
    rows = [row("tom@abc.com", "Tom Reyes", "Marketing Manager"), row("sarah@abc.com", "Sarah Lee", "Founder")]
    assert owner_first_name(lead(emails=rows), "tom@abc.com") == "Tom"
    assert owner_first_name(lead(emails=rows), "info@abc.com") == "Sarah"
    assert clean_first_name("JOHN SMITH") == "John"


def test_greeting_is_rewritten():
    assert apply_greeting("<p>Hi ABC Plumbing team,</p><p>I noticed...</p>", "") == "<p>Hi there,</p><p>I noticed...</p>"
    assert apply_greeting("<p>Hello Sarah!</p><p>x</p>", "Sarah") == "<p>Hi Sarah,</p><p>x</p>"
    assert apply_greeting("<p>I noticed...</p>", "Tom") == "<p>Hi Tom,</p><p>I noticed...</p>"  # added when missing
    assert apply_greeting_text("Hi Acme Bakery,\n\nQuick one.", "") == "Hi there,\n\nQuick one."

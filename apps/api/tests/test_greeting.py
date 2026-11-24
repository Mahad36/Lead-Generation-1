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


def test_text_scraped_next_to_an_address_is_not_a_name():
    # Kathe's Jewelry was greeted "Hi New," from "New York" on the website
    rows = [row("kathesjewelry@gmail.com", "New York"), row("info@kathesjewelry.com", "New York")]
    assert owner_first_name(lead(business="Kathe's Jewelry", emails=rows), "kathesjewelry@gmail.com") == ""
    for address, junk in [("owner@a.com", "Water Street"), ("info@a.com", "Submit Thanks"), ("hello@a.com", "Hello World"),
                          ("mail@a.com", "Book Appointment"), ("studio@a.com", "Studio Bella")]:
        assert owner_first_name(lead(emails=[row(address, junk)]), address) == ""
    # a title doesn't make it a person either: "Svelta Dental Design, Founder" on a shared inbox
    assert owner_first_name(lead(emails=[row("info@a.com", "Svelta Dental Design", "Founder")]), "info@a.com") == ""


def test_a_name_the_address_backs_up_is_used():
    for address in ("sarah@a.com", "sarah.lee@a.com", "sarahlee@a.com", "slee@a.com", "lee.sarah@a.com"):
        assert owner_first_name(lead(emails=[row(address, "Sarah Lee")]), address) == "Sarah"
    assert owner_first_name(lead(emails=[row("jose@a.com", "José Ruiz")]), "jose@a.com") == "José"
    assert owner_first_name(lead(emails=[row("alan@a.com", "Al Green")]), "alan@a.com") == ""  # too short to be sure
    branch = lead(emails=[row("stillorgan@a.com", "Stillorgan Shopping Centre")])
    branch.address = "Stillorgan Shopping Centre, Dublin"
    assert owner_first_name(branch, "stillorgan@a.com") == ""  # a branch mailbox, not a person
    # a contact name you entered or imported yourself is always trusted
    assert owner_first_name(lead("Maria Lopez", emails=[row("info@a.com", "New York")]), "info@a.com") == "Maria"


def test_greeting_is_rewritten():
    assert apply_greeting("<p>Hi ABC Plumbing team,</p><p>I noticed...</p>", "") == "<p>Hi there,</p><p>I noticed...</p>"
    assert apply_greeting("<p>Hello Sarah!</p><p>x</p>", "Sarah") == "<p>Hi Sarah,</p><p>x</p>"
    assert apply_greeting("<p>I noticed...</p>", "Tom") == "<p>Hi Tom,</p><p>I noticed...</p>"  # added when missing
    assert apply_greeting_text("Hi Acme Bakery,\n\nQuick one.", "") == "Hi there,\n\nQuick one."

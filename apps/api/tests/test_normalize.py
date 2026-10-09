from app.utils.normalize import (
    extract_domain,
    normalize_address,
    normalize_business_name,
    normalize_email,
    normalize_phone,
)


def test_business_name_strips_legal_suffixes():
    assert normalize_business_name("ABC Plumbing LLC") == normalize_business_name("ABC Plumbing, LLC")
    assert normalize_business_name("XYZ Dental Inc.") == "xyz dental"


def test_phone_normalization():
    assert normalize_phone("(305) 555-1212") == "3055551212"
    assert normalize_phone("+1 305-555-1212") == "3055551212"


def test_email_and_domain():
    assert normalize_email("  Info@Example.COM ") == "info@example.com"
    assert extract_domain("https://www.abcplumbing.com/contact") == "abcplumbing.com"
    assert extract_domain("abcplumbing.com") == "abcplumbing.com"


def test_address_normalization():
    assert normalize_address("123 Main St, Miami, FL") == normalize_address("123 Main St Miami FL")

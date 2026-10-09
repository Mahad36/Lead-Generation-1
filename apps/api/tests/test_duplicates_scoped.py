from app.services.duplicates import find_duplicate_lead


def test_duplicate_lookup_requires_user_id():
    assert find_duplicate_lead.__code__.co_varnames[1] == "user_id"

from app.utils.security import (
    create_access_token,
    create_oauth_state_token,
    create_unsubscribe_token,
    try_decode_token,
)


def test_oauth_state_is_not_an_access_token():
    token = create_oauth_state_token("user-a")
    payload = try_decode_token(token)
    assert payload is not None
    assert payload["type"] == "oauth_state"
    assert payload["sub"] == "user-a"
    assert payload["type"] != "access"


def test_access_token_cannot_be_used_as_oauth_state():
    token = create_access_token("user-a")
    payload = try_decode_token(token)
    assert payload["type"] == "access"
    assert payload["type"] != "oauth_state"


def test_unsubscribe_token_is_user_and_email_scoped():
    token = create_unsubscribe_token("user-a", "lead@example.test")
    payload = try_decode_token(token)
    assert payload["type"] == "unsubscribe"
    assert payload["sub"] == "user-a"
    assert payload["email"] == "lead@example.test"
    other = try_decode_token(create_unsubscribe_token("user-b", "lead@example.test"))
    assert other["sub"] != payload["sub"]

from app.utils.security import create_access_token, hash_password, try_decode_token, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("Sup3rSecret!")
    assert hashed != "Sup3rSecret!"
    assert verify_password("Sup3rSecret!", hashed)
    assert not verify_password("wrong", hashed)


def test_jwt_roundtrip():
    token = create_access_token("user-1")
    payload = try_decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["type"] == "access"

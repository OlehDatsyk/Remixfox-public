"""Signing in with a password: set while signed in, or given to an admin by the owner."""

from app import passwords
from tests.conftest import OWNER, sign_in_by_email, sign_up


def login(client, email, password):
    client.cookies.clear()
    return client.post("/api/auth/password/login", json={"email": email, "password": password})


def test_a_password_is_set_after_signing_in_and_then_works(client):
    me = sign_up(client, "someone@example.com")
    assert me["has_password"] is False
    assert client.post("/api/me/password", json={"password": "short"}).status_code == 422
    assert client.post("/api/me/password", json={"password": "a long secret"}).json()["has_password"] is True

    assert login(client, "someone@example.com", "wrong secret").status_code == 401
    assert login(client, "nobody@example.com", "a long secret").status_code == 401  # same answer: no hints
    signed_in = login(client, "Someone@Example.com", "a long secret")
    assert signed_in.status_code == 200 and signed_in.json()["email"] == "someone@example.com"
    assert client.get("/api/me").json()["email"] == "someone@example.com"  # the session cookie is set


def test_only_a_hash_is_stored():
    stored = passwords.hash_password("a long secret")
    assert stored.startswith("scrypt$") and "a long secret" not in stored
    assert passwords.verify_password("a long secret", stored)
    assert not passwords.verify_password("a long secreT", stored)
    assert not passwords.verify_password("anything", "not a hash")


def test_wrong_passwords_are_limited(client):
    sign_up(client, "someone@example.com")
    client.post("/api/me/password", json={"password": "a long secret"})
    passwords._fails.clear()
    for _ in range(passwords.MAX_FAILS_PER_EMAIL):
        assert login(client, "someone@example.com", "guess").status_code == 401
    assert login(client, "someone@example.com", "a long secret").status_code == 429
    passwords._fails.clear()


def test_the_owner_gives_an_admin_a_password(client):
    assert client.post("/api/admins", json={"email": "helper@example.com", "password": "given by owner"}).status_code == 201
    assert client.get("/api/admins").json()[0] == {**client.get("/api/admins").json()[0], "has_password": True}
    admin = login(client, "helper@example.com", "given by owner")
    assert admin.status_code == 200 and admin.json()["role"] == "admin"

    sign_in_by_email(client, OWNER)
    assert client.put("/api/admins/helper@example.com/password", json={"password": "a new one!"}).status_code == 204
    assert login(client, "helper@example.com", "given by owner").status_code == 401
    assert login(client, "helper@example.com", "a new one!").status_code == 200
    assert client.put("/api/admins/nobody@example.com/password", json={"password": "a new one!"}).status_code == 403

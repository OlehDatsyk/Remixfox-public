"""Sign-in through the Telegram bot, with Telegram's Bot API faked."""

import logging
from collections import defaultdict, deque
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth, telegram
from app.db import engine
from app.main import app
from app.models import Competitor, TelegramLogin, User, utcnow
from tests.conftest import OWNER, sign_up

ALICE = {"id": 5550001, "is_bot": False, "first_name": "Alice", "last_name": "Fox", "username": "alicefox"}
PHOTO = b"\xff\xd8\xff\xe0alice-photo"


@pytest.fixture
def bot(monkeypatch):
    """A configured bot; .sent holds its replies, .photos says whether users have a photo."""
    configured = replace(telegram.settings, telegram_bot_token="123:test-token")
    monkeypatch.setattr(telegram, "settings", configured)
    monkeypatch.setattr(telegram, "_bot", {})
    monkeypatch.setattr(telegram, "_starts", defaultdict(deque))  # each test starts with no sign-ins counted

    class Fake:
        sent: list[dict] = []
        calls: list[str] = []
        photos = True

    fake = Fake()
    fake.sent, fake.calls = [], []

    def call(method, **params):
        fake.calls.append(method)
        if method == "getMe":
            return {"username": "remixfox_bot"}
        if method == "sendMessage":
            fake.sent.append(params)
            return {"message_id": len(fake.sent)}
        if method == "getUserProfilePhotos":
            sizes = [{"file_id": f"f{w}", "width": w, "height": w} for w in (160, 320, 640)]
            return {"photos": [sizes] if fake.photos else []}
        if method == "getFile":
            return {"file_path": f"photos/{params['file_id']}.jpg"}
        raise AssertionError(method)

    monkeypatch.setattr(telegram, "call", call)
    monkeypatch.setattr(telegram, "download", lambda path: PHOTO if path == "photos/f160.jpg" else b"wrong size")
    return fake


def press_start(client, sender: dict = ALICE, payload: str = "", secret: str | None = None):
    text = f"/start {payload}".strip()
    update = {"update_id": 1, "message": {"message_id": 1, "from": sender, "chat": {"id": sender["id"], "type": "private"}, "text": text}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret or telegram.webhook_secret()}
    return client.post("/api/telegram/webhook", json=update, headers=headers)


def start_on_website(client, connect: bool = False) -> str:
    response = client.post(f"/api/auth/telegram/start{'?connect=true' if connect else ''}")
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert url.startswith("https://t.me/remixfox_bot?start=")
    return response.json()["token"]


def test_signing_in_from_the_website(client, bot):
    client.cookies.clear()
    token = start_on_website(client)
    assert client.post("/api/auth/telegram/finish", json={"token": token}).status_code == 202  # waiting

    assert press_start(client, payload=token).json() == {"ok": True}
    assert "You are signed in to Remixfox, Alice Fox" in bot.sent[-1]["text"]

    done = client.post("/api/auth/telegram/finish", json={"token": token})
    assert done.status_code == 200 and done.json()["status"] == "signed_in"
    me = client.get("/api/me").json()
    assert (me["name"], me["telegram_username"], me["telegram_connected"], me["email"]) == ("Alice Fox", "alicefox", True, None)
    assert (me["is_owner"], me["can_fetch"]) == (False, True)  # the free trial
    avatar = client.get(me["avatar_url"])
    assert (avatar.status_code, avatar.content) == (200, PHOTO)  # the 160 px photo

    # The login is used up (and the waiting cookie gone); signing in again finds the same account.
    assert client.post("/api/auth/telegram/finish", json={"token": token}).status_code == 404
    client.cookies.clear()
    token = start_on_website(client)
    press_start(client, payload=token)
    assert client.post("/api/auth/telegram/finish", json={"token": token}).json()["me"]["name"] == "Alice Fox"


def test_a_leaked_bot_link_signs_nobody_else_in(client, bot):
    client.cookies.clear()
    token = start_on_website(client)
    press_start(client, payload=token)
    other_browser = TestClient(app)  # has the token from the link, but not the waiting browser's cookie
    assert other_browser.post("/api/auth/telegram/finish", json={"token": token}).status_code == 404
    assert client.post("/api/auth/telegram/finish", json={"token": token}).status_code == 200


def test_the_webhook_only_listens_to_telegram(client, bot):
    assert press_start(client, secret="wrong").status_code == 403
    assert bot.sent == []


def test_start_in_the_bot_gives_a_one_time_link(client, bot):
    bot.photos = False
    press_start(client)
    reply = bot.sent[-1]
    assert reply["text"].startswith("Hi Alice Fox! Tap the button to sign in")
    button = reply["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "Sign in to Remixfox"
    link = urlparse(button["url"])
    path = f"{link.path}?{link.query}"

    client.cookies.clear()
    opened = client.get(path, follow_redirects=False)
    assert (opened.status_code, opened.headers["location"]) == (303, "/")
    me = client.get("/api/me").json()
    assert (me["name"], me["avatar_url"]) == ("Alice Fox", None)  # no profile photo

    client.cookies.clear()
    again = client.get(path, follow_redirects=False)
    assert again.headers["location"] == "/welcome.html?telegram=expired"
    assert client.get("/api/me").status_code == 401
    assert parse_qs(link.query)["token"]


def test_an_expired_start_link_is_explained_in_the_chat(client, bot):
    client.cookies.clear()
    token = start_on_website(client)
    with Session(engine) as session:
        login = session.query(TelegramLogin).one()
        login.expires_at = utcnow()
        session.commit()
    press_start(client, payload=token)
    assert bot.sent[-1]["text"].startswith("This sign-in link has expired")
    assert client.post("/api/auth/telegram/finish", json={"token": token}).status_code == 410


def test_connecting_telegram_to_an_e_mail_account(client, bot):
    token = start_on_website(client, connect=True)  # the owner, signed in by e-mail
    press_start(client, payload=token)
    assert "Telegram is now connected to your Remixfox account" in bot.sent[-1]["text"]
    assert client.post("/api/auth/telegram/finish", json={"token": token}).json()["status"] == "connected"
    me = client.get("/api/me").json()
    assert (me["email"], me["telegram_username"], me["is_owner"]) == (OWNER, "alicefox", True)

    # From now on Telegram opens the same (owner's) account.
    client.cookies.clear()
    token = start_on_website(client)
    press_start(client, payload=token)
    assert client.post("/api/auth/telegram/finish", json={"token": token}).json()["me"]["email"] == OWNER


def test_trying_telegram_first_does_not_block_connecting_it(client, bot):
    client.cookies.clear()
    token = start_on_website(client)
    press_start(client, payload=token)  # a Telegram-only account, with nothing in it
    client.post("/api/auth/telegram/finish", json={"token": token})

    sign_up(client, "someone@example.com")
    token = start_on_website(client, connect=True)
    press_start(client, payload=token)
    assert "Telegram is now connected" in bot.sent[-1]["text"]
    assert client.post("/api/auth/telegram/finish", json={"token": token}).json()["status"] == "connected"
    with Session(engine) as session:
        assert session.query(User).count() == 2  # the owner and someone; the empty account is gone


def test_a_telegram_account_connects_to_only_one_remixfox_account(client, bot):
    client.cookies.clear()
    token = start_on_website(client)
    press_start(client, payload=token)  # Alice has her own Telegram account on Remixfox
    client.post("/api/auth/telegram/finish", json={"token": token})
    with Session(engine) as session:  # ... with something in it
        alice = session.query(User).filter(User.telegram_id == ALICE["id"]).one()
        session.add(Competitor(username="natgeo", user_id=alice.id))
        session.commit()

    sign_up(client, "someone@example.com")
    token = start_on_website(client, connect=True)
    press_start(client, payload=token)
    assert "already has its own Remixfox account" in bot.sent[-1]["text"]
    assert client.post("/api/auth/telegram/finish", json={"token": token}).status_code == 409
    assert client.get("/api/me").json()["telegram_connected"] is False


def test_telegram_sign_in_waits_for_its_token(client, monkeypatch):
    monkeypatch.setattr(telegram, "settings", replace(telegram.settings, telegram_bot_token=""))
    assert client.post("/api/auth/telegram/start").status_code == 503
    client.cookies.clear()
    monkeypatch.setattr(telegram, "settings", replace(telegram.settings, telegram_bot_token="123:test-token"))
    assert client.post("/api/auth/telegram/start?connect=true").status_code == 401
    assert auth  # the session cookie comes from app.auth.sign_in


def test_sign_ins_from_one_network_are_limited_and_old_ones_tidied(client, bot):
    with Session(engine) as session:
        session.add(TelegramLogin(token_hash="old" * 10, expires_at=utcnow() - timedelta(days=2)))
        session.commit()
    for _ in range(telegram.PER_IP_PER_HOUR):
        start_on_website(client)
    too_many = client.post("/api/auth/telegram/start")
    assert too_many.status_code == 429 and "Too many sign-ins" in too_many.json()["detail"]
    with Session(engine) as session:
        assert session.exec(select(TelegramLogin).where(TelegramLogin.token_hash == "old" * 10)).first() is None


def test_request_urls_are_not_logged_as_they_carry_the_bot_token():
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)

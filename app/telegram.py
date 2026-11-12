"""Sign-in through the Telegram bot (TELEGRAM_BOT_TOKEN). No passwords, no e-mail.

From the website ("Continue with Telegram"):
  1. POST /api/auth/telegram/start: makes a one-time login token, which goes in
     the bot link (t.me/<bot>?start=<token>), and a secret for this browser,
     which goes in a 10-minute cookie. The page opens the link.
  2. In Telegram the user presses Start; Telegram sends "/start <token>" to our
     webhook, POST /api/telegram/webhook (checked by its secret header).
  3. The server takes the user's Telegram id, name, @username and profile photo,
     finds or creates their account, marks the login confirmed and replies in
     the chat.
  4. The page polls POST /api/auth/telegram/finish {token}; with the token and
     this browser's cookie it gets the 30-day session. Neither works alone, so a
     leaked bot link cannot sign anyone else in.

From the bot (Start without a token, or /login): the bot answers with a
one-time "Sign in to Remixfox" button, GET /api/auth/telegram/link?token=...
(10 minutes), which signs in whichever browser opens it.

Connecting: a signed-in user presses "Connect Telegram" in Settings (the same
flow with ?connect=true), so e-mail and Telegram open the same account.

The webhook is registered when the app starts (setup()), with a secret derived
from SECRET_KEY that Telegram sends back in a header.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete
from sqlmodel import Session, select

from app.auth import OptionalUser, SessionDep, me, set_cookie, sign_in, token_hash
from app.config import settings
from app.models import Avatar, Competitor, TelegramFinish, TelegramLogin, User, utcnow

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
LOGIN_MINUTES = 10
BROWSER_COOKIE = "remixfox_tg"
AVATAR_SIZE = 160  # px: the smallest photo size at least this wide
PER_IP_PER_HOUR = 20  # sign-ins started from one network, as for e-mail codes

_starts: dict[str, deque] = defaultdict(deque)
_starts_lock = threading.Lock()

router = APIRouter(prefix="/api")


class TelegramError(Exception):
    """The Bot API refused a call or could not be reached."""


def configured() -> bool:
    return bool(settings.telegram_bot_token)


def call(method: str, **params) -> dict | list | bool:
    try:
        response = httpx.post(f"{API}/bot{settings.telegram_bot_token}/{method}", json=params, timeout=20)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramError(f"{method}: {type(exc).__name__}") from exc
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'refused')}")
    return data["result"]


def download(file_path: str) -> bytes:
    try:
        response = httpx.get(f"{API}/file/bot{settings.telegram_bot_token}/{file_path}", timeout=20)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TelegramError(f"download: {type(exc).__name__}") from exc
    return response.content


_bot: dict = {}


def bot_username() -> str:
    if "username" not in _bot:
        _bot["username"] = call("getMe")["username"]
    return _bot["username"]


def webhook_secret() -> str:
    return hmac.new(settings.secret_key.encode(), b"telegram-webhook", hashlib.sha256).hexdigest()


def setup() -> None:
    """Point the bot's webhook at this server (when the app starts)."""
    if not (configured() and settings.public_url):
        return
    try:
        call(
            "setWebhook", url=f"{settings.public_url}/api/telegram/webhook",
            secret_token=webhook_secret(), allowed_updates=["message"],
        )
        log.info("Telegram webhook set for @%s", bot_username())
    except TelegramError as exc:
        log.warning("Could not set the Telegram webhook: %s", exc)


# ---------- Telegram users ----------

def save_profile(session: Session, user: User, sender: dict) -> None:
    """Name, @username and profile photo, as Telegram has them now."""
    user.telegram_id = sender["id"]
    user.name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))[:200] or user.name
    user.telegram_username = sender.get("username")
    session.add(user)
    session.commit()
    try:
        photos = call("getUserProfilePhotos", user_id=sender["id"], limit=1)["photos"]
        if not photos:
            return
        sizes = sorted(photos[0], key=lambda p: p["width"])
        size = next((p for p in sizes if p["width"] >= AVATAR_SIZE), sizes[-1])
        data = download(call("getFile", file_id=size["file_id"])["file_path"])
    except (TelegramError, KeyError, IndexError) as exc:
        log.warning("No Telegram photo for user %s: %s", user.id, exc)
        return
    avatar = session.get(Avatar, user.id) or Avatar(user_id=user.id, content_type="image/jpeg", data=data)
    avatar.data, avatar.content_type, avatar.updated_at = data, "image/jpeg", utcnow()
    session.add(avatar)
    session.commit()


def is_empty(session: Session, user: User) -> bool:
    """A Telegram-only account with nothing in it (no competitors, never paid)."""
    has_competitors = session.exec(select(Competitor.id).where(Competitor.user_id == user.id)).first()
    return user.email is None and user.stripe_customer_id is None and not has_competitors


def find_or_create(session: Session, sender: dict) -> User:
    user = session.exec(select(User).where(User.telegram_id == sender["id"])).first()
    if user is None:
        user = User(telegram_id=sender["id"])
        session.add(user)
        session.commit()
        session.refresh(user)
    save_profile(session, user, sender)
    return user


def send(chat_id: int, text: str, button: tuple[str, str] | None = None) -> None:
    params = {"chat_id": chat_id, "text": text}
    if button:
        params["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    try:
        call("sendMessage", **params)
    except TelegramError as exc:
        log.warning("Could not reply in Telegram: %s", exc)


def _greeting(user: User) -> str:
    return user.name or (f"@{user.telegram_username}" if user.telegram_username else "there")


# ---------- Bot: what happens when someone presses Start ----------

def confirm_web_login(session: Session, sender: dict, chat_id: int, token: str, base: str) -> None:
    login = session.exec(select(TelegramLogin).where(TelegramLogin.token_hash == token_hash(token))).first()
    if login is None or login.browser_hash is None or login.used_at or login.expires_at < utcnow():
        send(chat_id, "This sign-in link has expired. Go back to Remixfox and press “Continue with Telegram” again.")
        return
    if login.user_id:
        send(chat_id, "You are already signed in. Go back to your browser.")
        return
    if login.connect_user_id:
        taken = session.exec(select(User).where(User.telegram_id == sender["id"])).first()
        if taken and taken.id != login.connect_user_id:
            if is_empty(session, taken):  # e.g. someone tried Telegram first: just move Telegram over
                session.delete(taken)
                session.commit()
            else:
                login.error = "This Telegram account already has its own Remixfox account."
                session.commit()
                send(chat_id, login.error + " Sign in with Telegram to use that one.")
                return
        user = session.get(User, login.connect_user_id)
        save_profile(session, user, sender)
        done = f"Telegram is now connected to your Remixfox account, {_greeting(user)}. Go back to your browser."
    else:
        user = find_or_create(session, sender)
        done = f"You are signed in to Remixfox, {_greeting(user)}. Go back to your browser."
    login.user_id = user.id
    session.commit()
    send(chat_id, f"✅ {done}", ("Open Remixfox", f"{base}/"))


def send_login_link(session: Session, sender: dict, chat_id: int, base: str) -> None:
    user = find_or_create(session, sender)
    token = secrets.token_urlsafe(24)
    session.add(TelegramLogin(
        token_hash=token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(minutes=LOGIN_MINUTES),
    ))
    session.commit()
    send(
        chat_id,
        f"Hi {_greeting(user)}! Tap the button to sign in to Remixfox. "
        f"The link works once, for {LOGIN_MINUTES} minutes; send /login for a new one.",
        ("Sign in to Remixfox", f"{base}/api/auth/telegram/link?token={token}"),
    )


@router.post("/telegram/webhook", include_in_schema=False)
def webhook(
    request: Request,
    session: SessionDep,
    update: Annotated[dict, Body()],
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict:
    if not configured() or not hmac.compare_digest(x_telegram_bot_api_secret_token or "", webhook_secret()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not from Telegram")
    message = update.get("message") or {}
    sender, chat = message.get("from") or {}, message.get("chat") or {}
    if chat.get("type") != "private" or not sender.get("id") or sender.get("is_bot"):
        return {"ok": True}
    base = settings.public_url or str(request.base_url).rstrip("/")
    command, _, argument = (message.get("text") or "").strip().partition(" ")
    if command == "/start" and argument.strip():
        confirm_web_login(session, sender, chat["id"], argument.strip(), base)
    elif command in ("/start", "/login"):
        send_login_link(session, sender, chat["id"], base)
    else:
        send(chat["id"], "Send /login and I will give you a button to sign in to Remixfox.")
    return {"ok": True}  # always 200, or Telegram keeps resending the update


# ---------- Website ----------

def too_many_starts(ip: str | None) -> bool:
    """True past PER_IP_PER_HOUR sign-ins started from one network (each one stores a row);
    counted in memory, which is enough for one process."""
    if not ip:
        return False
    now = time.monotonic()
    with _starts_lock:
        recent = _starts[ip]
        while recent and recent[0] < now - 3600:
            recent.popleft()
        if len(recent) >= PER_IP_PER_HOUR:
            return True
        recent.append(now)
        return False


@router.post("/auth/telegram/start")
def start(request: Request, response: Response, session: SessionDep, user: OptionalUser, connect: bool = False) -> dict:
    """A bot link for this browser; with connect=true, links Telegram to the signed-in account."""
    if not configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram sign-in is not set up yet")
    if connect and user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue")
    try:
        bot = bot_username()
    except TelegramError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram is not answering. Try again in a minute.") from exc
    ip = request.client.host if request.client else None
    if too_many_starts(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-ins from this network. Try again in an hour.")
    session.exec(delete(TelegramLogin).where(TelegramLogin.expires_at < utcnow() - timedelta(days=1)))  # tidy up
    token, browser = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    expires_at = utcnow() + timedelta(minutes=LOGIN_MINUTES)
    session.add(TelegramLogin(
        token_hash=token_hash(token), browser_hash=token_hash(browser),
        connect_user_id=user.id if connect else None, expires_at=expires_at,
    ))
    session.commit()
    set_cookie(request, response, BROWSER_COOKIE, browser, LOGIN_MINUTES * 60)
    return {"url": f"https://t.me/{bot}?start={token}", "token": token, "expires_at": expires_at}


@router.post("/auth/telegram/finish")
def finish(
    payload: TelegramFinish,
    request: Request,
    response: Response,
    session: SessionDep,
    remixfox_tg: Annotated[str | None, Cookie()] = None,
) -> dict:
    """Polled by the waiting page: 202 while waiting, then the session (200)."""
    login = session.exec(select(TelegramLogin).where(TelegramLogin.token_hash == token_hash(payload.token))).first()
    if login is None or not remixfox_tg or not hmac.compare_digest(login.browser_hash or "", token_hash(remixfox_tg)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Start again with “Continue with Telegram”.")
    if login.error:
        raise HTTPException(status.HTTP_409_CONFLICT, login.error)
    if login.used_at or (login.user_id is None and login.expires_at < utcnow()):
        raise HTTPException(status.HTTP_410_GONE, "This sign-in has expired. Press “Continue with Telegram” again.")
    if login.user_id is None:
        response.status_code = status.HTTP_202_ACCEPTED
        return {"status": "waiting"}
    login.used_at = utcnow()
    session.commit()
    user = session.get(User, login.user_id)
    response.delete_cookie(BROWSER_COOKIE, path="/")
    if login.connect_user_id:
        return {"status": "connected", "me": me(session, user)}
    return {"status": "signed_in", "me": sign_in(session, request, response, user)}


@router.get("/auth/telegram/link", include_in_schema=False)
def link(token: str, request: Request, session: SessionDep) -> Response:
    """The bot's one-time "Sign in to Remixfox" button."""
    login = session.exec(select(TelegramLogin).where(TelegramLogin.token_hash == token_hash(token))).first()
    if login is None or login.browser_hash or not login.user_id or login.used_at or login.expires_at < utcnow():
        return RedirectResponse("/welcome.html?telegram=expired", status.HTTP_303_SEE_OTHER)
    login.used_at = utcnow()
    session.commit()
    response = RedirectResponse("/", status.HTTP_303_SEE_OTHER)
    sign_in(session, request, response, session.get(User, login.user_id))
    return response

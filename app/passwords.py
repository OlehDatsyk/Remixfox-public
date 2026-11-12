"""Passwords: an optional way in, next to the e-mail code and the Telegram bot.

A password is set by someone already signed in (so the e-mail is proven by its
code first), or by the owner for an admin, who then signs in with it. Only an
scrypt hash is stored. Failed tries are limited per e-mail and per IP; the counts
live in memory, which is enough for one process.
"""

import base64
import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import select

from app.auth import CurrentUser, SessionDep, me, normalise_email, sign_in
from app.models import Me, PasswordLogin, PasswordSet, User

router = APIRouter(prefix="/api")

MIN_LENGTH = 8
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
WINDOW_SECONDS = 15 * 60
MAX_FAILS_PER_EMAIL = 10
MAX_FAILS_PER_IP = 30

_fails: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    encode = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(expected),
                                n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Use at least {MIN_LENGTH} characters")
    if not password.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The password cannot be only spaces")


def _recent(key: str) -> deque:
    fails = _fails[key]
    while fails and fails[0] < time.monotonic() - WINDOW_SECONDS:
        fails.popleft()
    return fails


# Checked against when the e-mail has no password, so a wrong e-mail takes as long as a wrong password.
_DUMMY = hash_password(os.urandom(12).hex())


@router.post("/auth/password/login")
def login(payload: PasswordLogin, request: Request, response: Response, session: SessionDep) -> Me:
    email = normalise_email(payload.email)
    ip = f"ip:{request.client.host if request.client else 'unknown'}"  # as for e-mail codes
    with _lock:
        if len(_recent(email)) >= MAX_FAILS_PER_EMAIL or len(_recent(ip)) >= MAX_FAILS_PER_IP:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Too many wrong passwords. Wait 15 minutes, or sign in with a code by e-mail.")
    user = session.exec(select(User).where(User.email == email)).first()
    stored = user.password_hash if user and user.password_hash else None
    if not verify_password(payload.password, stored or _DUMMY) or stored is None:
        with _lock:
            _fails[email].append(time.monotonic())
            _fails[ip].append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong e-mail or password")
    return sign_in(session, request, response, user)


@router.post("/me/password")
def set_password(payload: PasswordSet, session: SessionDep, user: CurrentUser) -> Me:
    """Set or change your password (you are signed in, so your e-mail or Telegram is proven)."""
    if not user.email:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Add an e-mail first: you sign in with your e-mail and this password")
    check_strength(payload.password)
    user.password_hash = hash_password(payload.password)
    session.commit()
    return me(session, user)

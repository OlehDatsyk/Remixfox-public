"""Accounts: sessions, who is signed in, and who may do what. No passwords.

People sign in with a one-time code sent by e-mail (app/email_login.py) or
through the Telegram bot (app/telegram.py); both end in sign_in() here, which
sets the session cookie. The first sign-in creates the account.

- Every signed-in user sees only their own competitors, reels and plan.
- Fetching reels and transcripts costs money, so it needs a plan (app/plans.py):
  the owner (OWNER_EMAIL) has no limits and pays nothing; admins (up to 10
  e-mails the owner adds) get the Studio plan's limits for free and can see
  every account to help people; everyone else gets what their paid plan allows.
"""

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete
from sqlmodel import Session, select

from app import plans, translations
from app.config import settings
from app.db import get_session
from app.models import ACTIVE_SUBSCRIPTIONS, Admin, Allowance, Avatar, Competitor, LoginSession, Me, User, utcnow

COOKIE = "remixfox_session"
SESSION_DAYS = 30
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SessionDep = Annotated[Session, Depends(get_session)]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_cookie(request: Request, response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, path="/", httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
    )


def normalise_email(raw: str) -> str:
    email = raw.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a valid e-mail address")
    return email


# ---------- Who is asking ----------

def _session_user(session: Session, token: str | None) -> User | None:
    if token:
        login = session.exec(select(LoginSession).where(LoginSession.token_hash == token_hash(token))).first()
        if login and login.expires_at > utcnow():
            return session.get(User, login.user_id)
    return None


def current_user(session: SessionDep, remixfox_session: Annotated[str | None, Cookie()] = None) -> User:
    user = _session_user(session, remixfox_session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue")
    return user


def optional_user(session: SessionDep, remixfox_session: Annotated[str | None, Cookie()] = None) -> User | None:
    return _session_user(session, remixfox_session)


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]


def is_owner(user: User) -> bool:
    """The owner is whoever has OWNER_EMAIL, so changing it moves ownership at once."""
    return bool(settings.owner_email) and user.email == settings.owner_email


def is_admin(session: Session, user: User) -> bool:
    return bool(user.email) and session.exec(select(Admin.id).where(Admin.email == user.email)).first() is not None


def role(session: Session, user: User) -> str:
    return "owner" if is_owner(user) else "admin" if is_admin(session, user) else "user"


def allowance(session: Session, user: User) -> Allowance | None:
    """What this account may use: the owner's, an admin's, the paid plan's, or the free trial."""
    who = role(session, user)
    if who == "owner":
        return plans.OWNER
    if who == "admin":
        return plans.ADMIN
    if user.subscription_status in ACTIVE_SUBSCRIPTIONS:
        return plans.of_plan(plans.PLANS.get(user.plan or "", plans.PLANS["start"]))
    return plans.TRIAL


def paying_user(user: CurrentUser, session: SessionDep) -> User:
    if allowance(session, user) is None:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Choose a plan to fetch reels and transcripts")
    return user


def owner_user(user: CurrentUser) -> User:
    if not is_owner(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can do this")
    return user


def staff_user(user: CurrentUser, session: SessionDep) -> User:
    if role(session, user) == "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner and admins can do this")
    return user


PayingUser = Annotated[User, Depends(paying_user)]
OwnerUser = Annotated[User, Depends(owner_user)]
StaffUser = Annotated[User, Depends(staff_user)]


def label(user: User) -> str:
    """How a user is shown: e-mail, else name and @username from Telegram."""
    handle = f"@{user.telegram_username}" if user.telegram_username else None
    return user.email or " ".join(filter(None, [user.name, handle])) or f"Telegram user {user.telegram_id}"


def me(session: Session, user: User) -> Me:
    avatar = session.get(Avatar, user.id)
    allowed = allowance(session, user)
    return Me(
        email=user.email,
        name=user.name,
        telegram_username=user.telegram_username,
        telegram_connected=user.telegram_id is not None,
        avatar_url=f"/api/me/avatar?v={int(avatar.updated_at.timestamp())}" if avatar else None,
        role=role(session, user),
        is_owner=is_owner(user),
        created_at=user.created_at,
        subscription_status=user.subscription_status,
        subscription_renews_at=user.subscription_renews_at,
        plan=user.plan,
        plan_interval=user.plan_interval,
        allowance=allowed,
        usage=plans.usage(session, user.id, allowed),
        can_fetch=allowed is not None and not plans.trial_over(session, user, allowed),
        billing_ready=bool(settings.stripe_secret_key),
        translate_to=user.translate_to if user.translate_to in translations.LANGUAGES else "en",
        languages=translations.LANGUAGES,
        has_password=bool(user.password_hash),
        trial_ends_at=plans.trial_ends_at(user) if allowed and allowed.trial else None,
        remix_profile=user.remix_profile,
    )


# ---------- Signing in ----------

def claim_old_data(session: Session, user: User) -> None:
    """Competitors from before accounts existed (no user) belong to the owner."""
    if is_owner(user):
        for competitor in session.exec(select(Competitor).where(Competitor.user_id.is_(None))).all():
            competitor.user_id = user.id
        session.commit()


def sign_in(session: Session, request: Request, response: Response, user: User) -> Me:
    """Start a 30-day session for this browser (only a hash of the token is stored)."""
    claim_old_data(session, user)
    session.exec(delete(LoginSession).where(LoginSession.expires_at < utcnow()))  # tidy up expired sessions
    token = secrets.token_urlsafe(32)
    session.add(LoginSession(
        token_hash=token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(days=SESSION_DAYS),
    ))
    session.commit()
    set_cookie(request, response, COOKIE, token, SESSION_DAYS * 86400)
    return me(session, user)


# ---------- Endpoints ----------

router = APIRouter(prefix="/api")


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, session: SessionDep, remixfox_session: Annotated[str | None, Cookie()] = None) -> None:
    if remixfox_session:
        login = session.exec(select(LoginSession).where(LoginSession.token_hash == token_hash(remixfox_session))).first()
        if login:
            session.delete(login)
            session.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
def read_me(user: CurrentUser, session: SessionDep) -> Me:
    return me(session, user)


@router.get("/me/avatar", responses={200: {"content": {"image/*": {}}}})
def read_avatar(user: CurrentUser, session: SessionDep) -> Response:
    avatar = session.get(Avatar, user.id)
    if avatar is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile photo")
    # The URL carries a version, so the browser can keep it.
    return Response(avatar.data, media_type=avatar.content_type, headers={"Cache-Control": "private, max-age=31536000"})

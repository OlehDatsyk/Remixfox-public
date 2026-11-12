"""Sign-in with a one-time code sent by e-mail: any address, no password.

1. POST /api/auth/email/start {email}: a 6-digit code is e-mailed (valid 10
   minutes; only the newest code counts).
2. POST /api/auth/email/verify {email, code}: signs in; the first time, this
   creates the account.

Guessing is pointless: 5 tries per code, a new code at most once a minute and
5 an hour per address, 20 an hour per IP address. Only a keyed hash of each
code is stored.

E-mail is sent with Resend's HTTP API (RESEND_API_KEY, from MAIL_FROM on a
domain verified in Resend), because Render's free plan blocks SMTP. Without a
key the code is written to the server log instead: that is how local runs work,
and how the owner can still sign in (from Render's logs) before e-mail is set up.
"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, func
from sqlmodel import select

from app.auth import SessionDep, normalise_email, sign_in
from app.config import settings
from app.models import EmailStart, EmailVerify, LoginCode, Me, User, utcnow

log = logging.getLogger(__name__)

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_WAIT = timedelta(seconds=60)
PER_EMAIL_PER_HOUR = 5
PER_IP_PER_HOUR = 20

router = APIRouter(prefix="/api")


def _hash(email: str, code: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def deliver(email: str, code: str) -> None:
    """Send the code (or, with no e-mail service configured, log it)."""
    if not settings.resend_api_key:
        log.warning("E-mail is not set up (RESEND_API_KEY), so here is the sign-in code for %s: %s", email, code)
        return
    text = (
        f"Your Remixfox sign-in code is {code}\n\n"
        f"It works for {CODE_MINUTES} minutes. If you did not ask for it, you can ignore this e-mail."
    )
    html = (
        '<div style="font-family:Arial,sans-serif;color:#15142b;max-width:420px">'
        '<p style="font-size:18px;font-weight:700;margin:0 0 12px">Your Remixfox sign-in code</p>'
        f'<p style="font-size:34px;font-weight:800;letter-spacing:6px;margin:0 0 16px;color:#5b4ff0">{code}</p>'
        f'<p style="color:#5d5c76;margin:0">It works for {CODE_MINUTES} minutes. '
        "If you did not ask for it, you can ignore this e-mail.</p></div>"
    )
    problem = None
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.mail_from, "to": [email], "subject": f"{code} is your Remixfox code", "text": text, "html": html},
            timeout=20,
        )
        if response.status_code >= 400:
            problem = f"Resend refused it ({response.status_code}): {response.text[:300]}"
    except httpx.HTTPError as exc:
        problem = f"Resend could not be reached: {exc}"
    if problem:
        # Logged with the code, so the owner can still sign in (from Render's logs),
        # for example while the domain is not yet verified in Resend.
        log.warning("Could not e-mail the sign-in code for %s: %s. The code is %s", email, problem, code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not send the e-mail. Try again in a minute.")


@router.post("/auth/email/start", status_code=status.HTTP_204_NO_CONTENT)
def start(payload: EmailStart, request: Request, session: SessionDep) -> None:
    email = normalise_email(payload.email)
    now = utcnow()
    hour_ago = now - timedelta(hours=1)
    latest = session.exec(
        select(LoginCode).where(LoginCode.email == email).order_by(LoginCode.id.desc())
    ).first()
    if latest and latest.created_at > now - RESEND_WAIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "A code is on its way. You can ask for a new one in a minute.")
    sent = session.exec(
        select(func.count(LoginCode.id)).where(LoginCode.email == email, LoginCode.created_at > hour_ago)
    ).one()
    if sent >= PER_EMAIL_PER_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many codes for this address. Try again in an hour.")
    ip = request.client.host if request.client else None
    if ip and session.exec(
        select(func.count(LoginCode.id)).where(LoginCode.ip == ip, LoginCode.created_at > hour_ago)
    ).one() >= PER_IP_PER_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many codes from this network. Try again in an hour.")

    session.exec(delete(LoginCode).where(LoginCode.expires_at < now - timedelta(days=1)))  # tidy up
    code = f"{secrets.randbelow(10**6):06d}"
    session.add(LoginCode(
        email=email, code_hash=_hash(email, code), ip=ip, expires_at=now + timedelta(minutes=CODE_MINUTES),
    ))
    session.commit()
    deliver(email, code)


@router.post("/auth/email/verify")
def verify(payload: EmailVerify, request: Request, response: Response, session: SessionDep) -> Me:
    email = normalise_email(payload.email)
    code = re.sub(r"\D", "", payload.code)
    row = session.exec(  # only the newest code counts, and only once
        select(LoginCode).where(LoginCode.email == email).order_by(LoginCode.id.desc())
    ).first()
    wrong = HTTPException(status.HTTP_400_BAD_REQUEST, "That code is wrong or has expired. Ask for a new one.")
    if row is None or row.used_at or row.expires_at < utcnow() or row.attempts >= MAX_ATTEMPTS:
        raise wrong
    if not hmac.compare_digest(row.code_hash, _hash(email, code)):
        row.attempts += 1
        session.commit()
        raise wrong
    row.used_at = utcnow()
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email)
        session.add(user)
    session.commit()
    session.refresh(user)
    return sign_in(session, request, response, user)

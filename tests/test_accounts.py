"""Accounts: sign-in with e-mail codes, each user's own data, and who may fetch.

Sign-in through Telegram is in test_telegram.py.
"""

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlmodel import Session, select

from app import email_login, plans
from app.db import engine
from app.models import Competitor, LoginCode, utcnow
from tests.conftest import OWNER, REAL_DELIVER, SENT_CODES, reel_item, sign_in_by_email, sign_up


def ask_code(client, email: str) -> int:
    return client.post("/api/auth/email/start", json={"email": email}).status_code


def enter_code(client, email: str, code: str) -> int:
    return client.post("/api/auth/email/verify", json={"email": email, "code": code}).status_code


def subscribe(email: str, status: str = "active") -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET subscription_status = :s WHERE email = :e"), {"s": status, "e": email})


def test_registering_signs_you_in(client):
    me = sign_up(client, "Someone@Example.com")
    assert (me["email"], me["is_owner"], me["can_fetch"]) == ("someone@example.com", False, True)  # the free trial
    assert (me["allowance"]["label"], me["allowance"]["trial"]) == ("Free trial", True)
    assert client.get("/api/me").json()["email"] == "someone@example.com"


def test_the_owner_is_the_owner_email(client):
    me = client.get("/api/me").json()
    assert (me["email"], me["is_owner"], me["can_fetch"]) == (OWNER, True, True)


def test_a_code_signs_in_without_a_password(client):
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401
    assert ask_code(client, OWNER) == 204
    assert len(SENT_CODES[OWNER]) == 6 and SENT_CODES[OWNER].isdigit()
    assert enter_code(client, OWNER, "000000" if SENT_CODES[OWNER] != "000000" else "111111") == 400
    assert client.get("/api/me").status_code == 401

    # Spaces and capitals do not matter; the same account comes back.
    me = client.post("/api/auth/email/verify", json={"email": OWNER.upper(), "code": " ".join(SENT_CODES[OWNER])}).json()
    assert (me["email"], me["is_owner"]) == (OWNER, True)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_a_code_works_once_and_only_the_newest_counts(client):
    client.cookies.clear()
    ask_code(client, "someone@example.com")
    first = SENT_CODES["someone@example.com"]
    ask_code(client, "someone@example.com")
    newest = SENT_CODES["someone@example.com"]
    if first != newest:
        assert enter_code(client, "someone@example.com", first) == 400
    assert enter_code(client, "someone@example.com", newest) == 200
    client.cookies.clear()
    assert enter_code(client, "someone@example.com", newest) == 400  # used
    assert enter_code(client, "someone@example.com", first) == 400  # older ones never come back


def test_codes_expire_and_allow_five_tries(client):
    client.cookies.clear()
    ask_code(client, "someone@example.com")
    code = SENT_CODES["someone@example.com"]
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(5):
        assert enter_code(client, "someone@example.com", wrong) == 400
    assert enter_code(client, "someone@example.com", code) == 400  # locked after five wrong tries

    ask_code(client, "someone@example.com")
    with Session(engine) as session:
        latest = session.exec(select(LoginCode).order_by(LoginCode.id.desc())).first()
        latest.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    assert enter_code(client, "someone@example.com", SENT_CODES["someone@example.com"]) == 400


def test_asking_for_codes_is_limited(client, monkeypatch):
    client.cookies.clear()
    assert ask_code(client, "not-an-e-mail") == 422
    monkeypatch.setattr(email_login, "RESEND_WAIT", timedelta(seconds=60))
    assert ask_code(client, "someone@example.com") == 204
    assert ask_code(client, "someone@example.com") == 429  # a new code once a minute
    monkeypatch.setattr(email_login, "RESEND_WAIT", timedelta(0))
    for _ in range(4):
        assert ask_code(client, "someone@example.com") == 204
    assert ask_code(client, "someone@example.com") == 429  # five an hour per address
    monkeypatch.setattr(email_login, "PER_IP_PER_HOUR", 6)  # the owner's code and these five
    assert ask_code(client, "another@example.com") == 429  # and only so many from one network


def test_without_resend_the_code_goes_to_the_log(client, monkeypatch, caplog):
    monkeypatch.setattr(email_login, "deliver", REAL_DELIVER)  # RESEND_API_KEY is empty in tests
    client.cookies.clear()
    assert ask_code(client, "someone@example.com") == 204
    assert any("someone@example.com" in r.getMessage() and "sign-in code" in r.getMessage() for r in caplog.records)


def test_everything_but_the_front_end_needs_signing_in(client):
    client.cookies.clear()
    for path in ("/api/me", "/api/reels", "/api/competitors", "/api/plan", "/api/jobs"):
        assert client.get(path).status_code == 401, path
    assert client.get("/api/health").status_code == 200
    assert client.get("/welcome.html").status_code == 200
    for page in ("/privacy.html", "/terms.html", "/legal.html", "/robots.txt", "/sitemap.xml", "/og-image.png"):
        assert client.get(page).status_code == 200, page
    assert "Sitemap: https://remixfox.co.uk/sitemap.xml" in client.get("/robots.txt").text


def test_every_response_carries_the_security_headers(client):
    page, api = client.get("/welcome.html"), client.get("/api/health")
    for response in (page, api):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    policy = page.headers["content-security-policy"]
    assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "content-security-policy" not in client.get("/api/docs").headers  # Swagger UI loads from a CDN


def test_search_engines_see_only_the_public_pages_with_true_prices(client):
    client.cookies.clear()
    landing = client.get("/").text
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', landing, re.S).group(1))
    offer = next(item for item in data["@graph"] if item["@type"] == "SoftwareApplication")["offers"]
    monthly = [plan.monthly_pence / 100 for plan in plans.PLANS.values()]
    assert (float(offer["lowPrice"]), float(offer["highPrice"]), int(offer["offerCount"])) == (min(monthly), max(monthly), len(monthly))
    assert '<link rel="canonical" href="https://remixfox.co.uk/">' in landing
    assert 'name="robots" content="noindex' in client.get("/index.html").text  # the app itself is never indexed
    missing = client.get("/no-such-page")
    assert missing.status_code == 404 and "This page does not exist" in missing.text  # a real page, not JSON
    assert client.get("/api/no-such-endpoint").headers["content-type"] == "application/json"
    sitemap = client.get("/sitemap.xml").text
    for url in re.findall(r"<loc>https://remixfox.co.uk/(.*?)</loc>", sitemap):
        assert client.get(f"/{url}").status_code == 200, url
        assert url in ("", "legal.html", "privacy.html", "terms.html"), url


def test_the_address_itself_is_the_landing_page_for_visitors(client):
    signed_in = client.get("/")
    assert 'id="deck"' in signed_in.text  # the app
    assert "Cookie" in signed_in.headers["vary"]
    client.cookies.clear()
    landing = client.get("/")
    assert landing.status_code == 200 and 'id="typed"' in landing.text and 'id="deck"' not in landing.text
    assert 'href="privacy.html"' in landing.text and 'href="terms.html"' in landing.text
    assert 'id="faq"' in landing.text and 'property="og:image"' in landing.text


def test_the_owner_takes_over_data_from_before_accounts(client):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users"))
    with Session(engine) as session:
        session.add(Competitor(username="nick_saraev"))  # added when there were no accounts
        session.commit()
    sign_up(client, OWNER)
    assert [c["username"] for c in client.get("/api/competitors").json()] == ["nick_saraev"]


def test_the_free_trial_is_15_days_with_two_competitors_five_best_reels_each(client, fake_apify, auto_transcribe):
    sign_up(client, "someone@example.com")
    fake_apify.items = [reel_item(f"R{i}", plays=i * 100) for i in range(1, 9)]  # 8 reels, R8 most viewed
    fake_apify.transcripts = {f"R{i}": f"words {i}" for i in range(1, 9)}
    assert client.post("/api/competitors", json={"handle": "natgeo"}).status_code == 201
    kept = sorted(r["shortcode"] for r in client.get("/api/reels").json())
    assert kept == ["R4", "R5", "R6", "R7", "R8"]  # the five most viewed, with transcripts
    assert sorted(fake_apify.transcript_calls[0]) == kept
    me = client.get("/api/me").json()
    assert (me["usage"]["videos"], me["allowance"]["videos"], me["allowance"]["competitors"]) == (5, 10, 2)
    assert me["trial_ends_at"][:10] == (datetime.fromisoformat(me["created_at"]) + timedelta(days=15)).date().isoformat()

    assert client.post("/api/competitors", json={"handle": "nasa"}).status_code == 201  # a second competitor
    third = client.post("/api/competitors", json={"handle": "bbc"})
    assert third.status_code == 402 and "Free trial plan tracks up to 2 competitors" in third.json()["detail"]

    for competitor in client.get("/api/competitors").json():  # a day later, a second try for each
        with engine.begin() as conn:
            conn.execute(text("UPDATE competitors SET last_fetched_at = last_fetched_at - interval '25 hours'"))
        assert client.post(f"/api/competitors/{competitor['id']}/fetch").status_code == 202
    assert client.get("/api/me").json()["can_fetch"] is False
    with engine.begin() as conn:
        conn.execute(text("UPDATE competitors SET last_fetched_at = last_fetched_at - interval '25 hours'"))
    used = client.post(f"/api/competitors/{competitor['id']}/fetch")
    assert used.status_code == 402 and used.json()["detail"].startswith("Your free trial is used")

    subscribe("someone@example.com")
    assert client.get("/api/me").json()["allowance"]["label"] == "Start"
    assert client.post("/api/competitors", json={"handle": "bbc"}).status_code == 201


def test_the_free_trial_ends_after_15_days(client, fake_apify):
    sign_up(client, "someone@example.com")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET created_at = now() - interval '16 days' WHERE email = 'someone@example.com'"))
    assert client.get("/api/me").json()["can_fetch"] is False
    ended = client.post("/api/competitors", json={"handle": "natgeo"})
    assert ended.status_code == 402 and ended.json()["detail"].startswith("Your 15-day free trial has ended")


def test_each_user_sees_only_their_own_reels(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    owners_reel = client.get("/api/reels").json()[0]
    client.post(f"/api/reels/{owners_reel['id']}/plan")

    sign_up(client, "someone@example.com")
    for path in ("/api/reels", "/api/competitors", "/api/plan", "/api/jobs"):
        assert client.get(path).json() == [], path
    assert client.get(f"/api/reels/{owners_reel['id']}").status_code == 404
    assert client.get(f"/api/reels/{owners_reel['id']}/cover").status_code == 404
    assert client.patch(f"/api/reels/{owners_reel['id']}", json={"hook": "Mine now"}).status_code == 404
    assert client.delete(f"/api/reels/{owners_reel['id']}").status_code == 404

    # The same competitor can be followed by both, each with their own copy.
    subscribe("someone@example.com")
    assert client.post("/api/competitors", json={"handle": "natgeo"}).status_code == 201
    mine = client.get("/api/reels").json()
    assert len(mine) == 2 and owners_reel["id"] not in {r["id"] for r in mine}
    assert all(r["plan_status"] is None for r in mine)


def test_the_owner_follows_the_owner_email(client, monkeypatch):
    from dataclasses import replace

    from app import auth

    sign_up(client, "new.owner@example.com")
    assert client.get("/api/me").json()["is_owner"] is False
    with Session(engine) as session:
        session.add(Competitor(username="nick_saraev"))  # from before accounts
        session.commit()

    monkeypatch.setattr(auth, "settings", replace(auth.settings, owner_email="new.owner@example.com"))
    client.post("/api/auth/logout")
    sign_in_by_email(client, "new.owner@example.com")
    me = client.get("/api/me").json()
    assert (me["is_owner"], me["can_fetch"]) == (True, True)
    assert [c["username"] for c in client.get("/api/competitors").json()] == ["nick_saraev"]


def test_the_owner_sees_every_account_and_fetch(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    sign_up(client, "someone@example.com")
    subscribe("someone@example.com")
    client.post("/api/competitors", json={"handle": "nasa"})
    someones_job = client.get("/api/jobs").json()[0]["id"]
    assert client.get("/api/owner/overview").status_code == 403

    client.post("/api/auth/logout")
    sign_in_by_email(client, OWNER)
    overview = client.get("/api/owner/overview").json()
    assert overview["totals"] == {"users": 2, "subscribed": 1, "competitors": 2, "reels": 4, "active_jobs": 0}
    assert [(u["email"], u["owner"], u["competitors"], u["reels"]) for u in overview["users"]] == [
        ("someone@example.com", False, 1, 2), (OWNER, True, 1, 2),
    ]
    assert [(j["username"], j["account"], j["status"]) for j in overview["jobs"]] == [
        ("nasa", "someone@example.com", "succeeded"), ("natgeo", OWNER, "succeeded"),
    ]
    # The owner can open anyone's fetch to see its steps; nobody else can.
    assert client.get(f"/api/jobs/{someones_job}").json()["username"] == "nasa"
    assert client.get("/api/jobs").json()[0]["username"] == "natgeo"  # the owner's own list stays their own


def test_when_resend_refuses_the_code_is_logged_for_the_owner(client, monkeypatch, caplog):
    from dataclasses import replace

    import httpx

    monkeypatch.setattr(email_login, "deliver", REAL_DELIVER)
    monkeypatch.setattr(email_login, "settings", replace(email_login.settings, resend_api_key="re_test"))
    refused = httpx.Response(403, json={"message": "The remixfox.co.uk domain is not verified."})
    monkeypatch.setattr(email_login.httpx, "post", lambda *args, **kwargs: refused)
    client.cookies.clear()
    response = client.post("/api/auth/email/start", json={"email": "someone@example.com"})
    assert (response.status_code, response.json()["detail"]) == (502, "Could not send the e-mail. Try again in a minute.")
    logged = [r.getMessage() for r in caplog.records if "someone@example.com" in r.getMessage()]
    assert logged and "domain is not verified" in logged[0] and logged[0][-6:].isdigit()

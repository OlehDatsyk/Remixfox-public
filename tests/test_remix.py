"""AI remix: a reel rewritten in the user's own voice (OpenAI faked)."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app import ai
from app.db import engine
from app.models import Competitor, Reel, UsageEvent, utcnow
from tests.conftest import sign_up, user_id

REMIX = {"hook": "Most coaches waste their mornings.", "main_script": "Here is the routine I give my clients.",
         "cta": "Comment PLAN and I'll send it to you."}


@pytest.fixture
def fake_remix(monkeypatch):
    """OpenAI, faked: .prompts records what was asked, .answer is what comes back."""
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key="sk-test"))

    class Fake:
        prompts: list[str] = []
        schemas: list[str] = []
        answer: dict = {**REMIX, "usage": {"input_tokens": 600, "output_tokens": 900, "total_tokens": 1500}}

    fake = Fake()
    fake.prompts, fake.schemas = [], []

    def run(prompt, schema=None, name="reels"):
        fake.prompts.append(prompt)
        fake.schemas.append(name)
        return dict(fake.answer)

    monkeypatch.setattr(ai, "run_model", run)
    return fake


def a_reel(email=None, **fields) -> int:
    """One reel of @natgeo for the account (the owner by default)."""
    with Session(engine) as session:
        competitor = Competitor(username="natgeo", user_id=user_id(email) if email else user_id())
        session.add(competitor)
        session.flush()
        reel = Reel(competitor_id=competitor.id, shortcode="R1", views=120_000, **fields)
        session.add(reel)
        session.commit()
        return reel.id


def done(**fields) -> dict:
    return {"transcript": "Most people use Claude wrong.", "transcript_fetched_at": utcnow(), "translated_at": utcnow(),
            "translation_hook": "Most people use Claude wrong.", "translation_script": "Here is my setup.",
            "translation_cta": "Comment PLUGIN.", "translated_into": "en", **fields}


def test_a_reel_is_remixed_in_your_voice_and_nothing_is_saved(client, fake_remix):
    reel_id = a_reel(**done())
    voice = "Fitness coach for busy mums in the UK; friendly and direct; I sell a 12-week plan; CTA: comment PLAN"
    assert client.patch("/api/me", json={"remix_profile": voice}).json()["remix_profile"] == voice

    remix = client.post(f"/api/reels/{reel_id}/remix")
    assert remix.status_code == 200
    assert remix.json() == {"hook": REMIX["hook"], "script": REMIX["main_script"], "cta": REMIX["cta"],
                            "remixes_left": None}  # the owner has no limit
    prompt = fake_remix.prompts[0]
    assert fake_remix.schemas == ["remix"]
    payload = json.loads(prompt[prompt.index("{\n"):])
    assert payload["about_them"] == voice
    assert payload["original"]["hook"] == "Most people use Claude wrong." and payload["original"]["views"] == 120_000
    assert "in British English" in prompt and "British spelling" in prompt
    assert "Do not copy the original's sentences" in prompt

    reel = client.get(f"/api/reels/{reel_id}").json()
    assert (reel["hook"], reel["script"], reel["cta"]) == ("", "", "")  # your version changes only when you save
    with Session(engine) as session:
        [event] = session.exec(select(UsageEvent).where(UsageEvent.kind == "remix")).all()
    assert event.tokens == 1500 and round(event.cost_usd, 6) == round((600 * 0.20 + 900 * 1.20) / 1_000_000, 6)
    assert client.get("/api/me").json()["usage"]["remixes"] == 1


def test_a_remix_needs_something_to_remix(client, fake_remix):
    no_transcript = a_reel()
    assert client.post(f"/api/reels/{no_transcript}/remix").status_code == 409
    with engine.begin() as conn:  # a reel without speech is remixed from its caption
        conn.execute(text("UPDATE reels SET transcript = '', caption = 'Morning routine that changed my life'"))
    assert client.post(f"/api/reels/{no_transcript}/remix").status_code == 200
    assert '"caption": "Morning routine that changed my life"' in fake_remix.prompts[-1]


def test_remixes_are_written_in_your_language(client, fake_remix):
    reel_id = a_reel(**done(translated_into="es"))
    client.patch("/api/me", json={"translate_to": "es"})
    client.post(f"/api/reels/{reel_id}/remix")
    assert "in Spanish" in fake_remix.prompts[-1] and "British spelling" not in fake_remix.prompts[-1]


def test_each_plan_has_so_many_remixes(client, fake_remix):
    sign_up(client, "someone@example.com")
    reel_id = a_reel("someone@example.com", **done())
    with Session(engine) as session:  # the trial's 10 remixes, all used
        session.add_all(UsageEvent(user_id=user_id("someone@example.com"), kind="remix") for _ in range(10))
        session.commit()
    used = client.post(f"/api/reels/{reel_id}/remix")
    assert used.status_code == 402 and used.json()["detail"].startswith("The free trial's 10 AI remixes are used")

    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET subscription_status = 'active', plan = 'start' WHERE email = 'someone@example.com'"))
    remix = client.post(f"/api/reels/{reel_id}/remix").json()
    assert remix["remixes_left"] == 100 - 11  # the trial's ten count this month too
    assert [p["remixes"] for p in client.get("/api/billing/plans").json()["plans"]] == [100, 400, 1500]


def test_problems_are_explained(client, fake_remix, monkeypatch):
    reel_id = a_reel(**done())
    fake_remix.answer = {"hook": "", "main_script": "", "cta": ""}
    assert client.post(f"/api/reels/{reel_id}/remix").status_code == 502
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key=""))
    assert client.post(f"/api/reels/{reel_id}/remix").status_code == 503


def test_your_voice_has_a_length_limit(client):
    assert client.patch("/api/me", json={"remix_profile": "x" * 1001}).status_code == 422
    assert client.patch("/api/me", json={"remix_profile": "  "}).json()["remix_profile"] is None


def test_someone_elses_reel_cannot_be_remixed(client, fake_remix):
    reel_id = a_reel(**done())
    sign_up(client, "someone@example.com")
    assert client.post(f"/api/reels/{reel_id}/remix").status_code == 404

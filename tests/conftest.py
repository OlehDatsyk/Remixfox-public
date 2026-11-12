"""Tests run against a real PostgreSQL database (remixfox_test), never against Apify.

Locally: `docker compose up -d db`, then `pytest`. GitHub Actions starts its own Postgres.
"""

import json
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://remixfox:remixfox@127.0.0.1:5433/remixfox_test")
os.environ["APIFY_TOKEN"] = ""  # the real Apify is never called from tests
os.environ["AUTO_TRANSCRIBE"] = "false"  # tests that need it switch it on (see `auto_transcribe`)
os.environ["OWNER_EMAIL"] = "owner@example.com"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["AI_WORKER"] = "false"  # tests run the worker by hand (see `fake_ai`)
for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "OPENAI_API_KEY",
             "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PUBLIC_URL"):
    os.environ[name] = ""

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from datetime import timedelta  # noqa: E402

from app import apify, email_login, storage  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
FAKE_JPEG = b"\xff\xd8\xff\xe0fake-cover"
OWNER = "owner@example.com"

# E-mails are never sent from tests: the codes land here instead.
SENT_CODES: dict[str, str] = {}
REAL_DELIVER = email_login.deliver
email_login.deliver = lambda email, code: SENT_CODES.__setitem__(email, code)
email_login.RESEND_WAIT = timedelta(0)  # tests sign in again at once; one test checks the wait


def sign_in_by_email(client, email: str) -> dict:
    """Ask for a code and enter it (the first time, this creates the account)."""
    assert client.post("/api/auth/email/start", json={"email": email}).status_code == 204
    response = client.post("/api/auth/email/verify", json={"email": email, "code": SENT_CODES[email.lower()]})
    assert response.status_code == 200, response.text
    return response.json()


def sign_up(client, email: str) -> dict:
    """A fresh browser signing in (the client keeps the cookie)."""
    client.cookies.clear()
    return sign_in_by_email(client, email)


def user_id(email: str = OWNER) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email}).scalar_one()


def reel_item(shortcode: str, plays: int = 1000, **extra) -> dict:
    """A dataset item shaped like the Instagram Reel Scraper's output."""
    item = {
        "inputUrl": "https://www.instagram.com/natgeo",
        "id": f"id-{shortcode}",
        "type": "Video",
        "shortCode": shortcode,
        "caption": f"Caption of {shortcode}\nSecond line #travel",
        "hashtags": ["travel"],
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "commentsCount": 12,
        "displayUrl": f"https://scontent-fra5-1.cdninstagram.com/v/{shortcode}.jpg",
        "images": [
            f"https://instagram.flba3-1.fna.fbcdn.net/v/{shortcode}_p828x828.jpg",
            f"https://instagram.flba3-1.fna.fbcdn.net/v/{shortcode}_p320x320.jpg",
        ],
        "videoUrl": f"https://scontent-fra5-1.cdninstagram.com/o1/v/{shortcode}.mp4",
        "likesCount": 340,
        "videoViewCount": plays // 3,
        "videoPlayCount": plays,
        "timestamp": "2026-11-01T10:00:00.000Z",
        "ownerUsername": "natgeo",
        "videoDuration": 14.5,
        "productType": "clips",
    }
    item.update(extra)
    return item


def _create_test_database() -> None:
    url = make_url(settings.database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def migrate_from_scratch() -> None:
    config = Config(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def migrated():
    assert make_url(settings.database_url).database.endswith("_test"), "tests need a *_test database"
    _create_test_database()
    migrate_from_scratch()


@pytest.fixture
def client(migrated):
    """An API client on empty tables, signed in as the owner."""
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE users, sessions, admins, usage_events, competitors, reels, fetch_jobs, job_events, stored_files,"
            " avatars, login_codes, telegram_logins RESTART IDENTITY CASCADE"
        ))
    SENT_CODES.clear()
    with TestClient(app) as test_client:
        sign_up(test_client, OWNER)
        yield test_client


@pytest.fixture
def fake_apify(monkeypatch):
    """Replace the Apify call and cover downloads. Set .items to change the next result."""

    class Fake:
        items = [reel_item("REEL1", plays=5000), reel_item("REEL2", plays=900)]
        calls: list[tuple[str, int]] = []
        downloads: list[str] = []
        broken_urls: set[str] = set()  # downloads of these fail
        # Transcripts fetch_transcripts returns, by shortcode (missing: not returned).
        transcripts: dict[str, str] = {}
        transcript_calls: list[list[str]] = []
        transcript_error: str | None = None

    fake = Fake()
    fake.calls, fake.downloads, fake.broken_urls = [], [], set()
    fake.transcripts, fake.transcript_calls, fake.transcript_error = {}, [], None

    def fetch_reels(username: str, limit: int):
        fake.calls.append((username, limit))
        return fake.items

    def download_image(url: str):
        fake.downloads.append(url)
        return None if url in fake.broken_urls else (FAKE_JPEG, "image/jpeg")

    def fetch_transcripts(reels):
        fake.transcript_calls.append(list(reels))
        if fake.transcript_error:
            raise apify.ApifyError(fake.transcript_error)
        return {
            code: apify.parse_item(reel_item(code, transcript=fake.transcripts[code]))
            for code in reels if code in fake.transcripts
        }

    monkeypatch.setattr(apify, "fetch_reels", fetch_reels)
    monkeypatch.setattr(apify, "fetch_transcripts", fetch_transcripts)
    monkeypatch.setattr(storage, "download_image", download_image)
    return fake


@pytest.fixture
def auto_transcribe(monkeypatch):
    """Fetch jobs also get transcripts, as on the live site."""
    from dataclasses import replace

    from app import jobs

    monkeypatch.setattr(jobs, "settings", replace(settings, auto_transcribe=True))


@pytest.fixture
def fake_ai(monkeypatch):
    """The OpenAI API, faked: answers from .answers (transcript -> its split).

    The worker runs in the background on the server; tests call .process() instead.
    """
    from dataclasses import replace

    from app import ai

    class Fake:
        answers: dict[str, dict] = {}
        prompts: list[str] = []
        during = None  # called while the AI works, to look at the pipeline mid-run

        def process(self) -> int:
            done = 0
            while count := ai.process_once():
                done += count
            return done

    fake = Fake()
    fake.answers, fake.prompts = {}, []
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key="sk-test"))

    def run(prompt: str, schema=None, name="reels") -> dict:
        fake.prompts.append(prompt)
        if fake.during:
            fake.during()
        items = json.loads(prompt[len(ai.PROMPT):])
        return {"reels": [
            {"id": item["id"], **fake.answers[item["transcript"]]}
            for item in items if item["transcript"] in fake.answers
        ], "usage": {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}}

    monkeypatch.setattr(ai, "run_model", run)
    return fake

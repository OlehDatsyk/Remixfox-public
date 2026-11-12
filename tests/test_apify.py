"""The Apify client and the mapping of its output, without calling Apify."""

from dataclasses import replace

import httpx
import pytest

from app import apify, storage
from app.config import settings
from app.handles import parse_handle
from tests.conftest import reel_item


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(apify, "settings", replace(settings, apify_token="test-token"))


def test_fetch_reels_calls_the_reel_scraper(monkeypatch, token):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return httpx.Response(201, json=[reel_item("ABC")], request=httpx.Request("POST", url))

    monkeypatch.setattr(apify.httpx, "post", fake_post)

    items = apify.fetch_reels("natgeo", 20)

    assert items[0]["shortCode"] == "ABC"
    assert sent["url"] == "https://api.apify.com/v2/acts/apify~instagram-reel-scraper/run-sync-get-dataset-items"
    # The latest reels: pinned ones would be older reels shown first.
    assert sent["json"] == {"username": ["natgeo"], "resultsLimit": 20, "skipPinnedPosts": True}
    # The cap leaves room for the start fee, or Apify stops at 17 of 20 reels.
    assert sent["params"]["maxItems"] == 40
    assert "includeTranscript" not in sent["json"]  # paid add-on, never in bulk
    assert sent["headers"] == {"Authorization": "Bearer test-token"}

    # Apify refuses a cap whose maximum cost is under its minimum charge.
    apify.fetch_reels("natgeo", 3)
    assert sent["json"]["resultsLimit"] == 3
    assert sent["params"]["maxItems"] == 10


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "Apify rejected the token. Check APIFY_TOKEN."),
        (402, "The Apify account has run out of credit."),
        (408, "Apify took longer than 5 minutes. Try again later."),
        (400, "Apify returned an error (400): Input is not valid"),
    ],
)
def test_fetch_reels_explains_apify_errors(monkeypatch, token, status, message):
    body = {"error": {"type": "x", "message": "Input is not valid"}}
    monkeypatch.setattr(
        apify.httpx, "post",
        lambda url, **kw: httpx.Response(status, json=body, request=httpx.Request("POST", url)),
    )
    with pytest.raises(apify.ApifyError, match=message.replace("(", r"\(").replace(")", r"\)")):
        apify.fetch_reels("natgeo", 20)


def test_fetch_reels_needs_a_token():
    with pytest.raises(apify.ApifyError, match="not connected"):
        apify.fetch_reels("natgeo", 20)


def test_parse_item_maps_the_scraper_fields():
    reel = apify.parse_item(reel_item("ABC", plays=1_100_000, likesCount=-1))
    assert reel.shortcode == "ABC"
    assert reel.views == 1_100_000  # plays, which Instagram shows as views
    assert reel.likes == 0  # hidden like counts come back as -1
    assert reel.comments == 12
    assert reel.duration_seconds == 14.5
    assert reel.posted_at.isoformat() == "2026-11-01T10:00:00+00:00"
    assert [url.rsplit("/", 1)[-1] for url in reel.cover_source_urls] == ["ABC_p828x828.jpg", "ABC.jpg"]
    assert reel.owner_username == "natgeo"


def test_parse_item_falls_back_and_skips_errors():
    reel = apify.parse_item({"shortCode": "XYZ", "videoViewCount": 50, "videoPlayCount": None})
    assert (reel.views, reel.url, reel.caption, reel.posted_at, reel.cover_source_urls) == (
        50, "https://www.instagram.com/reel/XYZ/", "", None, [],
    )
    assert apify.parse_item({"error": "not_found"}) is None
    assert apify.parse_item({"caption": "no shortcode"}) is None
    assert apify.error_message([{"error": "no_items", "errorDescription": "Private account"}]) == "Private account"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("natgeo", "natgeo"),
        ("@NatGeo", "natgeo"),
        ("https://www.instagram.com/natgeo/", "natgeo"),
        ("instagram.com/nat.geo_1?igsh=abc", "nat.geo_1"),
        ("https://m.instagram.com/natgeo", "natgeo"),
        ("https://www.instagram.com/reel/C1234/", None),
        ("two words", None),
        ("", None),
        ("a" * 31, None),
    ],
)
def test_parse_handle(raw, expected):
    assert parse_handle(raw) == expected


def test_covers_only_come_from_instagram():
    assert storage.is_allowed_url("https://scontent-fra5-1.cdninstagram.com/v/a.jpg")
    assert storage.is_allowed_url("https://instagram.fkiv3-1.fna.fbcdn.net/v/a.jpg")
    assert not storage.is_allowed_url("http://scontent.cdninstagram.com/a.jpg")
    assert not storage.is_allowed_url("https://169.254.169.254/latest/meta-data")
    assert not storage.is_allowed_url("https://cdninstagram.com.evil.example/a.jpg")


def test_fetch_transcript_asks_for_one_reel_with_a_spending_cap(monkeypatch, token):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        item = reel_item("ABC", plays=7000, transcript="  Here is what I say.  ")
        return httpx.Response(201, json=[item], request=httpx.Request("POST", url))

    monkeypatch.setattr(apify.httpx, "post", fake_post)

    reel = apify.fetch_transcript("ABC", 43.2)

    assert reel.transcript == "Here is what I say."
    assert reel.views == 7000
    assert sent["json"] == {"username": ["https://www.instagram.com/reel/ABC/"], "includeTranscript": True}
    # One started minute: $0.001 run + $0.0026 reel + $0.048, plus a 25% margin.
    assert sent["params"]["maxTotalChargeUsd"] == 0.0645
    assert "maxItems" not in sent["params"]


def test_fetch_transcript_reports_a_missing_reel(monkeypatch, token):
    monkeypatch.setattr(
        apify.httpx, "post",
        lambda url, **kw: httpx.Response(201, json=[], request=httpx.Request("POST", url)),
    )
    with pytest.raises(apify.ApifyError, match="did not return this reel"):
        apify.fetch_transcript("ABC", 30)


@pytest.mark.parametrize(
    ("seconds", "cost"), [(43.2, 0.0516), (60, 0.0516), (61, 0.0996), (None, 0.1476)]
)
def test_transcript_cost_counts_started_minutes(seconds, cost):
    assert apify.transcript_cost_usd(seconds) == cost


def test_fetch_transcripts_asks_for_several_reels_in_one_run(monkeypatch, token):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        items = [reel_item("A", transcript="One."), reel_item("B", transcript="Two."), reel_item("X")]
        return httpx.Response(201, json=items, request=httpx.Request("POST", url))

    monkeypatch.setattr(apify.httpx, "post", fake_post)

    found = apify.fetch_transcripts({"A": 30, "B": 90, "C": 45})

    assert {code: reel.transcript for code, reel in found.items()} == {"A": "One.", "B": "Two."}  # X was not asked for
    assert sent["json"] == {
        "username": [apify.reel_link(c) for c in ("A", "B", "C")],
        "includeTranscript": True,
    }
    # One run: $0.001, plus 3 reels at $0.0026, plus 1 + 2 + 1 started minutes at $0.048.
    assert apify.transcripts_cost_usd([30, 90, 45]) == 0.2008
    assert sent["params"]["maxTotalChargeUsd"] == 0.251
    assert apify.transcripts_cost_usd([30] * 11) == round(2 * 0.001 + 11 * (0.0026 + 0.048), 4)  # two runs

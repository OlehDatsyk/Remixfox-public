"""Apify client: runs the Instagram Reel Scraper and normalises what it returns.

Actor: https://apify.com/apify/instagram-reel-scraper
Input:  {"username": ["natgeo"], "resultsLimit": 20, "skipPinnedPosts": true}
        or, for one reel's transcript, {"username": [reel URL], "includeTranscript": true}
Output: one dataset item per reel (shortCode, url, caption, displayUrl, videoUrl,
        videoPlayCount, videoViewCount, likesCount, commentsCount, videoDuration,
        timestamp, hashtags, ownerUsername, transcript, ...).

Free-plan prices (checked 8 November 2026): $0.001 per run, $0.0026 per reel,
and $0.048 per started minute of transcript. Transcripts are therefore fetched
one reel at a time, only when asked for.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.config import settings

API_BASE = "https://api.apify.com/v2"

# The sync endpoint gives up after 300 seconds; stop the run just before that
# so it is not left running (and billing) after we have stopped waiting.
RUN_TIMEOUT_SECONDS = 290

# maxItems is a safety cap on what a run can bill. Apify turns it into a
# spending limit that must also cover the run's start fee, so a cap equal to
# the limit stops runs short (17 of 20 reels). Twice the limit leaves room; the
# real cost is still set by resultsLimit. Apify also refuses caps below its
# minimum charge (about 4 reels), hence the floor.
MIN_ITEMS_CAP = 10

START_PRICE_USD = 0.001
REEL_PRICE_USD = 0.0026
RUN_PRICE_USD = START_PRICE_USD + REEL_PRICE_USD  # starting a run + the reel itself
TRANSCRIPT_PRICE_PER_MINUTE_USD = 0.048
# Reels per transcript run: about 10 s each, well inside the 5-minute limit.
TRANSCRIPT_BATCH_SIZE = 10


class ApifyError(Exception):
    """A problem worth showing to the user as the job's message."""


@dataclass
class ReelData:
    shortcode: str
    url: str
    caption: str = ""
    # Cover images to try in order: a smaller copy first, then the full size.
    cover_source_urls: list[str] = field(default_factory=list)
    video_url: str | None = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    duration_seconds: float | None = None
    posted_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    owner_username: str | None = None
    # Only present when the transcript add-on was on; None when there is no speech.
    transcript: str | None = None


def is_configured() -> bool:
    return bool(settings.apify_token)


def reel_link(shortcode: str) -> str:
    return f"https://www.instagram.com/reel/{shortcode}/"


def transcript_cost_usd(duration_seconds: float | None) -> float:
    """What one reel's transcript costs: a run, the reel and each started minute."""
    return round(START_PRICE_USD + _reel_transcript_usd(duration_seconds), 4)


def transcripts_cost_usd(durations: list[float | None]) -> float:
    """Several transcripts, fetched TRANSCRIPT_BATCH_SIZE reels per run."""
    runs = math.ceil(len(durations) / TRANSCRIPT_BATCH_SIZE)
    return round(runs * START_PRICE_USD + sum(map(_reel_transcript_usd, durations)), 4)


def _reel_transcript_usd(duration_seconds: float | None) -> float:
    minutes = max(1, math.ceil((duration_seconds or 180) / 60))
    return REEL_PRICE_USD + minutes * TRANSCRIPT_PRICE_PER_MINUTE_USD


def fetch_reels(username: str, limit: int) -> list[dict[str, Any]]:
    """The account's latest reels (pinned ones skipped), as raw dataset items."""
    return _run(
        {"username": [username], "resultsLimit": limit, "skipPinnedPosts": True},
        {"maxItems": max(2 * limit, MIN_ITEMS_CAP)},
    )


def fetch_transcript(shortcode: str, duration_seconds: float | None) -> ReelData:
    """One reel with its transcript (and fresh numbers), via its direct link."""
    found = fetch_transcripts({shortcode: duration_seconds})
    if shortcode not in found:
        raise ApifyError("Instagram did not return this reel. It may have been deleted.")
    return found[shortcode]


def fetch_transcripts(reels: dict[str, float | None]) -> dict[str, ReelData]:
    """Several reels with transcripts in one run: {shortcode: duration} in, {shortcode: data} out.

    Reels Instagram did not return are missing from the result.
    """
    items = _run(
        {"username": [reel_link(code) for code in reels], "includeTranscript": True},
        # A 25% margin over the expected cost, so a run can never cost more.
        {"maxTotalChargeUsd": round(transcripts_cost_usd(list(reels.values())) * 1.25, 4)},
    )
    found = {}
    for item in items:
        reel = parse_item(item)
        if reel is not None and reel.shortcode in reels:
            found[reel.shortcode] = reel
    return found


def _run(actor_input: dict[str, Any], limits: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the actor, wait for it and return its dataset items."""
    if not is_configured():
        raise ApifyError("Apify is not connected yet, so reels cannot be fetched. Set APIFY_TOKEN.")

    try:
        response = httpx.post(
            f"{API_BASE}/acts/{settings.apify_actor_id}/run-sync-get-dataset-items",
            params={"timeout": RUN_TIMEOUT_SECONDS, "format": "json", "clean": "true", **limits},
            headers={"Authorization": f"Bearer {settings.apify_token}"},
            json=actor_input,
            timeout=httpx.Timeout(RUN_TIMEOUT_SECONDS + 30, connect=15),
        )
    except httpx.HTTPError as exc:
        raise ApifyError(f"Could not reach Apify ({type(exc).__name__}). Try again later.") from exc

    if response.status_code in (401, 403):
        raise ApifyError("Apify rejected the token. Check APIFY_TOKEN.")
    if response.status_code == 402:
        raise ApifyError("The Apify account has run out of credit.")
    if response.status_code == 408:
        raise ApifyError("Apify took longer than 5 minutes. Try again later.")
    if response.status_code == 429:
        raise ApifyError("Apify is rate limiting us. Try again in a minute.")
    if response.status_code >= 400:
        raise ApifyError(f"Apify returned an error ({response.status_code}): {_error_message(response)}")

    try:
        items = response.json()
    except ValueError as exc:
        raise ApifyError("Apify returned a response that is not JSON.") from exc
    if not isinstance(items, list):
        raise ApifyError("Apify returned an unexpected response.")
    return items


def parse_item(item: Any) -> ReelData | None:
    """Map one dataset item to our fields; None for error items and non-reels."""
    if not isinstance(item, dict) or item.get("error"):
        return None
    shortcode = item.get("shortCode") or item.get("shortcode")
    if not isinstance(shortcode, str) or not shortcode:
        return None
    tags = item.get("hashtags")
    # images[0] is an 828 x 1472 copy (about 100 KB), sharp enough for the
    # preview; displayUrl is the full 1080 x 1920 frame (about 300 KB).
    images = item.get("images")
    covers = [images[0] if isinstance(images, list) and images else None, item.get("displayUrl")]
    return ReelData(
        shortcode=shortcode[:64],
        url=reel_link(shortcode),
        caption=_text(item.get("caption")),
        cover_source_urls=[url for url in map(_text, covers) if url],
        video_url=_text(item.get("videoUrl")) or None,
        # Instagram shows plays as a reel's views; videoViewCount is a smaller,
        # older metric, so it is only a fallback.
        views=_count(item.get("videoPlayCount")) or _count(item.get("videoViewCount")),
        likes=_count(item.get("likesCount")),
        comments=_count(item.get("commentsCount")),
        duration_seconds=_float(item.get("videoDuration")),
        posted_at=_datetime(item.get("timestamp")),
        tags=[str(t) for t in tags if t][:50] if isinstance(tags, list) else [],
        owner_username=_text(item.get("ownerUsername")) or None,
        transcript=_text(item.get("transcript")) or None,
    )


def error_message(items: list[Any]) -> str | None:
    """The first error the actor reported, e.g. for a private or unknown account."""
    for item in items:
        if isinstance(item, dict) and item.get("error"):
            return _text(item.get("errorDescription")) or _text(item.get("error"))
    return None


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200] or response.reason_phrase


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value: Any) -> int:
    # Hidden counts come back as -1 or null.
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

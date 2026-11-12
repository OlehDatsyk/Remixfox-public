"""Reel transcripts (what the author says), fetched one reel at a time.

Apify charges per started minute of audio, so a transcript is only fetched
when the user asks for it, is stored, and is never fetched twice.
"""

import threading

from sqlmodel import Session

from app import apify
from app.models import Reel, utcnow


class AlreadyFetching(Exception):
    """This reel's transcript is being fetched by another request."""


_in_progress: set[int] = set()
_guard = threading.Lock()


def transcribe(session: Session, reel: Reel) -> Reel:
    if reel.transcript is not None:
        return reel  # already paid for

    with _guard:
        if reel.id in _in_progress:
            raise AlreadyFetching
        _in_progress.add(reel.id)
    try:
        data = apify.fetch_transcript(reel.shortcode, reel.duration_seconds)
    finally:
        with _guard:
            _in_progress.discard(reel.id)

    # "" means the reel has no speech. A non-empty transcript now waits in the
    # AI queue (app/translations.py) for the AI worker (app/ai.py).
    reel.transcript = data.transcript or ""
    reel.transcript_fetched_at = utcnow()
    # The same run brings fresh numbers at no extra cost.
    reel.views, reel.likes, reel.comments = data.views, data.likes, data.comments
    reel.duration_seconds = data.duration_seconds or reel.duration_seconds
    reel.updated_at = utcnow()
    session.commit()
    session.refresh(reel)
    return reel

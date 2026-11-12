"""File storage: downloads covers and keeps them in the stored_files table.

Instagram's CDN links expire after a few days and refuse to load on other
sites, so covers are copied at fetch time and served from /api/reels/{id}/cover.
"""

import logging
from urllib.parse import urlparse

import httpx
from sqlmodel import Session, select

from app.models import Reel, StoredFile, utcnow

log = logging.getLogger(__name__)

COVER = "cover"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Only download from Instagram's own CDNs, never from arbitrary addresses.
ALLOWED_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host.endswith(ALLOWED_HOST_SUFFIXES)


def download_image(url: str) -> tuple[bytes, str] | None:
    """Return (bytes, content type), or None if it is not a usable image."""
    if not is_allowed_url(url):
        log.warning("Skipped a cover from an unexpected host: %s", urlparse(url).hostname)
        return None
    try:
        with httpx.stream("GET", url, timeout=20, follow_redirects=False) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    return None
            return bytes(data), content_type
    except httpx.HTTPError as exc:
        log.warning("Cover download failed: %s", type(exc).__name__)
        return None


def save_cover(session: Session, reel: Reel, source_urls: list[str]) -> int:
    """Download the first cover that works and point the reel at our copy.

    Returns its size in bytes, or 0 (keeping the old cover) if none could be downloaded.
    """
    for source_url in source_urls:
        downloaded = download_image(source_url)
        if downloaded is not None:
            break
    else:
        return 0
    data, content_type = downloaded
    stored = session.exec(
        select(StoredFile).where(StoredFile.reel_id == reel.id, StoredFile.kind == COVER)
    ).first()
    if stored is None:
        stored = StoredFile(reel_id=reel.id, kind=COVER, content_type=content_type, data=data)
        session.add(stored)
    stored.data, stored.content_type, stored.source_url = data, content_type, source_url
    stored.created_at = utcnow()
    # The version in the query string lets browsers cache each cover for good.
    reel.cover_url = f"/api/reels/{reel.id}/cover?v={int(stored.created_at.timestamp())}"
    return len(data)


def get_cover(session: Session, reel_id: int) -> StoredFile | None:
    return session.exec(
        select(StoredFile).where(StoredFile.reel_id == reel_id, StoredFile.kind == COVER)
    ).first()

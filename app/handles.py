"""Turn whatever the user pasted into an Instagram username."""

import re

_PROFILE_URL = re.compile(r"^(?:https?://)?(?:www\.|m\.)?instagram\.com/([^/?#]+)", re.IGNORECASE)
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")

# instagram.com/<this>/... is a page, not a profile.
_NOT_PROFILES = {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct", "about", "legal"}


def parse_handle(raw: str) -> str | None:
    """Accept "username", "@username" or an instagram.com profile link."""
    value = raw.strip()
    match = _PROFILE_URL.match(value)
    if match:
        value = match.group(1)
    value = value.removeprefix("@").lower()
    if not _USERNAME.match(value) or value in _NOT_PROFILES:
        return None
    return value


def profile_url(username: str) -> str:
    return f"https://www.instagram.com/{username}/"

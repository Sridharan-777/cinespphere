import re
from urllib.parse import parse_qs, urlparse


YOUTUBE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

VERIFIED_TRAILER_FALLBACKS = {
    "spider-man: brand new day": "https://www.youtube.com/embed/sU_ltjrFrQE",
    "the odyssey": "https://www.youtube.com/embed/f_bKjZeJBBI",
    "avengers: endgame": "https://www.youtube.com/embed/TcMBFSGVi1c",
}


def youtube_embed_url(value):
    """Normalize common YouTube links to an embeddable HTTPS URL."""
    value = str(value or "").strip()
    if not value:
        return None
    if YOUTUBE_KEY_PATTERN.fullmatch(value):
        return f"https://www.youtube.com/embed/{value}"
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    key = None
    if host == "youtu.be":
        key = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            key = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/")):
            key = parsed.path.strip("/").split("/")[1]
    return f"https://www.youtube.com/embed/{key}" if key and YOUTUBE_KEY_PATTERN.fullmatch(key) else None


def verified_trailer_for(title):
    """Return a verified studio embed for known legacy seed records."""
    return VERIFIED_TRAILER_FALLBACKS.get(str(title or "").strip().lower())

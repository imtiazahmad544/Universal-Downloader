"""URL and source canonicalization for dedup.

Design choices (documented):
- Query parameters and fragments are ALWAYS dropped. Share/tracking params
  (?utm_*, ?igsh, ?t=..., #fragments) differ between shares of the same
  media; dropping them lets one MediaItem/DownloadJob row represent the media
  regardless of which link discovered it.
- Hosts are lowercased; default ports (:80/:443) are dropped; a missing
  scheme defaults to https; trailing slashes are stripped except for the root
  path ("" and "/" both normalize to "/").
- canonicalize_source() maps the many ways a user can express a source
  (handle, @handle, full URL) to a single canonical_id per platform so the
  (customer_id, canonical_id) uniqueness constraint actually dedups.
"""

import re
from urllib.parse import urlsplit, urlunsplit

PLATFORMS = ("tiktok", "instagram", "youtube")

_HOST_RE = re.compile(r"[a-z0-9.-]+")
_NAME_BAD_CHARS = set(" /?#")


def _looks_like_url(value: str) -> bool:
    if "://" in value:
        return True
    lowered = value.lower()
    if lowered.startswith("www."):
        return True
    head = value.split("/", 1)[0]
    return "/" in value and "." in head


def _split_url(value: str):
    """Parse value as a URL, defaulting a missing scheme to https."""
    text = value.strip()
    if "://" not in text:
        text = "https://" + text
    return urlsplit(text)


def canonicalize_url(url: str) -> str:
    """Normalize a URL for dedup: scheme default, lowercase host, drop default
    ports, strip trailing slash (except root), drop query params + fragment."""
    text = (url or "").strip()
    if not text:
        raise ValueError("cannot canonicalize an empty URL")
    parts = _split_url(text)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    # Require a plausible public host (dotted) — anything else is unparseable.
    if not host or not _HOST_RE.fullmatch(host) or "." not in host:
        raise ValueError(f"cannot parse URL host from {url!r}")
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    if port is None:
        netloc = host
    elif ":" in host:  # IPv6 literal
        netloc = f"[{host}]:{port}"
    else:
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    # Query and fragment are intentionally dropped (see module docstring).
    return urlunsplit((scheme, netloc, path, "", ""))


def _validate_name(name: str, platform: str) -> str:
    if not name or any(ch.isspace() for ch in name) or any(ch in _NAME_BAD_CHARS for ch in name):
        raise ValueError(f"unparseable {platform} source name: {name!r}")
    return name


def _tiktok_canonical(value: str) -> str:
    if _looks_like_url(value):
        parts = _split_url(value)
        host = (parts.hostname or "").lower()
        if host != "tiktok.com" and not host.endswith(".tiktok.com"):
            raise ValueError(f"not a tiktok URL: {value!r}")
        segments = [s for s in parts.path.split("/") if s]
        if not segments or not segments[0].startswith("@"):
            # e.g. share links like vm.tiktok.com/XYZ carry no username
            raise ValueError(f"cannot extract a tiktok username from {value!r}")
        name = _validate_name(segments[0][1:], "tiktok")
        return f"tiktok:{name.lower()}"
    name = value[1:] if value.startswith("@") else value
    return f"tiktok:{_validate_name(name, 'tiktok').lower()}"


def _instagram_canonical(value: str) -> str:
    if _looks_like_url(value):
        parts = _split_url(value)
        host = (parts.hostname or "").lower()
        if host != "instagram.com" and not host.endswith(".instagram.com"):
            raise ValueError(f"not an instagram URL: {value!r}")
        segments = [s for s in parts.path.split("/") if s]
        if not segments:
            raise ValueError(f"cannot extract an instagram username from {value!r}")
        name = _validate_name(segments[0], "instagram")
        return f"instagram:{name.lower()}"
    name = value[1:] if value.startswith("@") else value
    return f"instagram:{_validate_name(name, 'instagram').lower()}"


def _youtube_canonical(value: str) -> str:
    if value.startswith("@"):
        handle = _validate_name(value[1:], "youtube")
        return f"youtube:@{handle.lower()}"
    if _looks_like_url(value):
        parts = _split_url(value)
        host = (parts.hostname or "").lower()
        if host == "youtube.com" or host.endswith(".youtube.com"):
            segments = [s for s in parts.path.split("/") if s]
            if segments and segments[0].startswith("@"):
                handle = _validate_name(segments[0][1:], "youtube")
                return f"youtube:@{handle.lower()}"
            if len(segments) >= 2 and segments[0] == "channel":
                channel_id = segments[1]
                if not channel_id:
                    raise ValueError(f"empty youtube channel id in {value!r}")
                # Channel IDs are case-SENSITIVE: never lowercase them.
                return f"youtube:channel:{channel_id}"
            if len(segments) >= 2 and segments[0] in ("c", "user"):
                name = _validate_name(segments[1], "youtube")
                return f"youtube:c:{name.lower()}"
        # Any other youtube URL shape (watch?v=, shorts/, youtu.be, ...) or a
        # non-youtube host: fall back to the canonical URL form.
        return "youtube:url:" + canonicalize_url(value)
    # Bare non-URL, non-handle input: canonical URL form.
    return "youtube:url:" + canonicalize_url(value)


def canonicalize_source(platform: str, input_value: str) -> str:
    """Map a user-supplied source to its canonical_id for (customer, platform).

    platform must be one of {"tiktok", "instagram", "youtube"}.
    Raises ValueError on unparseable input.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform!r} (expected one of {PLATFORMS})")
    value = (input_value or "").strip()
    if not value:
        raise ValueError("source input is empty")
    if platform == "tiktok":
        return _tiktok_canonical(value)
    if platform == "instagram":
        return _instagram_canonical(value)
    return _youtube_canonical(value)

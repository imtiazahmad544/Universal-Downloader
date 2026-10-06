"""Ultra link extractor with ordered fallbacks (v2.0).

Given a page/media URL, resolve a direct media URL plus metadata. Strategies
run in order; each is wrapped in try/except and the FIRST success wins:

    1. ytdlp          - yt-dlp default extraction (no cookies)
    2. ytdlp_cookies  - yt-dlp with a temp Netscape cookies file (--cookies)
    3. opengraph      - plain HTTP GET, parse og:video:url / og:video:secure_url
                        / twitter:player:stream meta tags
    4. oembed         - <link rel="alternate" type="application/json+oembed">
                        discovery, then pull a media URL from the response
    5. video_tag      - scrape <video>/<source> tags for direct mp4/webm URLs

The winning strategy name is returned on ExtractedMedia.strategy_name and is
logged (extractor.strategy_hit). When every strategy fails an ExtractionError
is raised carrying per-strategy errors; when any strategy hit a captcha /
bot-check wall, ExtractionError.captcha_detected is True so callers can park
the job with E_CAPTCHA instead of burning retries.

Captcha flow (documented here because several layers participate):
    extractor.is_captcha_error()  -> detects captcha/bot-check signals in
                                     yt-dlp errors and fetched page bodies
    extractor.mark_job_captcha()  -> sets jobs.captcha_required=True,
                                     jobs.last_error_code='E_CAPTCHA' and moves
                                     the job to RETRY_WAIT when the state
                                     machine allows (a retryable state)
    POST /api/v1/jobs/{id}/resume -> clears captcha_required and re-arms the
                                     job to READY; the next attempt resolves
                                     cookies via cookies.resolve_cookies_for_source
                                     and retries WITH them.
"""

from __future__ import annotations

import html as html_module
import os
import re
import tempfile
from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session

from app.services.state_machine import RETRY_WAIT, InvalidTransitionError, validate_transition

logger = structlog.get_logger("universal-downloader.extractor")

# Stable machine-readable error code stored on jobs.last_error_code.
E_CAPTCHA = "E_CAPTCHA"

# Substrings (lowercased) that indicate a captcha / bot-check wall rather
# than an ordinary fetch failure. Kept conservative: a plain HTTP 429 with
# none of these markers is rate limiting, not a captcha.
_CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "sign in to confirm",
    "confirm you're not a robot",
    "confirm you are not a robot",
    "are you a robot",
    "verify you are human",
    "verify you're human",
    "unusual traffic",
    "bot check",
    "bot-check",
    "perimeterx",
    "datadome",
)

_OG_VIDEO_PROPERTIES = (
    "og:video:secure_url",
    "og:video:url",
    "og:video",
    "twitter:player:stream",
)

_DIRECT_MEDIA_EXTS = (".mp4", ".webm", ".mov", ".m4v")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CaptchaDetected(Exception):
    """A strategy hit a captcha / bot-check wall (not an ordinary failure)."""


class ExtractionError(Exception):
    """Every fallback strategy failed.

    Attributes:
        url: the URL that was attempted.
        attempts: list of (strategy_name, error_summary).
        captcha_detected: True when any strategy hit a captcha/bot-check wall.
    """

    def __init__(
        self,
        url: str,
        attempts: list[tuple[str, str]],
        captcha_detected: bool = False,
    ) -> None:
        self.url = url
        self.attempts = attempts
        self.captcha_detected = captcha_detected
        summary = "; ".join(f"{name}: {err}" for name, err in attempts[:3])
        super().__init__(f"extraction failed for {url!r} [{summary}]")


@dataclass
class ExtractedMedia:
    """Result of a successful extraction."""

    url: str  # direct media URL (best effort; falls back to the page URL)
    title: str | None = None
    ext: str | None = None
    strategy_name: str = ""


def is_captcha_error(exc_or_text: Exception | str) -> bool:
    """True when the error text looks like a captcha / bot-check wall."""
    text = exc_or_text if isinstance(exc_or_text, str) else str(exc_or_text)
    lowered = text.lower()
    return any(marker in lowered for marker in _CAPTCHA_MARKERS)


def write_temp_cookies_file(cookies_text: str) -> str:
    """Write Netscape cookies text to a temp file for yt-dlp --cookies.

    Returns the file path. The caller MUST delete it afterwards
    (delete_temp_file); raw cookie values must never linger on disk.
    """
    fd, path = tempfile.mkstemp(prefix="ud_cookies_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(cookies_text)
    except BaseException:
        delete_temp_file(path)
        raise
    return path


def delete_temp_file(path: str | None) -> None:
    """Best-effort delete of a temp file (never raises)."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _meta_property(html: str, prop: str) -> str | None:
    """Extract <meta property|name="prop" content="..."> (either order)."""
    for pattern in (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return html_module.unescape(match.group(1)).strip()
    return None


def _guess_ext(url: str) -> str | None:
    lowered = url.lower().split("?")[0]
    for ext in _DIRECT_MEDIA_EXTS:
        if lowered.endswith(ext):
            return ext.lstrip(".")
    return None


def _netscape_to_cookie_dict(cookies_text: str) -> dict[str, str]:
    """Best-effort Netscape -> {name: value} for plain HTTP strategies.

    Domain scoping is intentionally ignored: these are the customer's own
    cookies being sent back to hosts they came from.
    """
    jar: dict[str, str] = {}
    for line in cookies_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) >= 7:
            jar[parts[5]] = parts[6]
    return jar


class LinkExtractor:
    """Ordered-fallback link extractor. See module docstring for the chain."""

    def __init__(self, timeout: float = 30.0, user_agent: str | None = None) -> None:
        self.timeout = timeout
        self.user_agent = user_agent or _DEFAULT_UA

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, url: str, cookies: str | None = None) -> ExtractedMedia:
        """Run the fallback chain; first success wins.

        Raises ExtractionError when every strategy fails (with
        captcha_detected=True when any strategy hit a captcha wall).
        """
        url = (url or "").strip()
        if not url:
            raise ExtractionError(url, [("validate", "empty url")])

        attempts: list[tuple[str, str]] = []
        captcha_seen = False

        chain: list[tuple[str, bool]] = [
            ("ytdlp", True),
            ("ytdlp_cookies", bool(cookies)),
            ("opengraph", True),
            ("oembed", True),
            ("video_tag", True),
        ]
        for name, enabled in chain:
            if not enabled:
                logger.debug("extractor.strategy_skipped", url=url, strategy=name)
                continue
            try:
                media = self._run_strategy(name, url, cookies)
            except CaptchaDetected as exc:
                captcha_seen = True
                attempts.append((name, f"captcha: {exc}"))
                logger.warning(
                    "extractor.strategy_captcha", url=url, strategy=name
                )
                continue
            except Exception as exc:  # each strategy is independent evidence
                attempts.append((name, str(exc)[:200]))
                logger.debug(
                    "extractor.strategy_failed",
                    url=url,
                    strategy=name,
                    error=str(exc)[:200],
                )
                continue
            logger.info("extractor.strategy_hit", url=url, strategy=name)
            return media

        raise ExtractionError(url, attempts, captcha_detected=captcha_seen)

    # ------------------------------------------------------------------
    # Strategy dispatch
    # ------------------------------------------------------------------

    def _run_strategy(
        self, name: str, url: str, cookies: str | None
    ) -> ExtractedMedia:
        if name == "ytdlp":
            return self._via_ytdlp(url, None)
        if name == "ytdlp_cookies":
            return self._via_ytdlp(url, cookies)
        if name == "opengraph":
            return self._via_opengraph(url, cookies)
        if name == "oembed":
            return self._via_oembed(url, cookies)
        if name == "video_tag":
            return self._via_video_tag(url, cookies)
        raise RuntimeError(f"unknown strategy: {name}")

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    def _via_ytdlp(self, url: str, cookies: str | None) -> ExtractedMedia:
        """yt-dlp extraction, optionally with a temp cookies file."""
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("yt-dlp not installed") from exc

        strategy = "ytdlp_cookies" if cookies else "ytdlp"
        ydl_opts: dict = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        cookie_path = write_temp_cookies_file(cookies) if cookies else None
        try:
            if cookie_path:
                ydl_opts["cookiefile"] = cookie_path
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            if is_captcha_error(exc):
                raise CaptchaDetected(str(exc)[:300]) from exc
            raise
        finally:
            delete_temp_file(cookie_path)

        if not info:
            raise RuntimeError("yt-dlp returned no metadata")
        if isinstance(info, dict) and info.get("_type") == "playlist":
            entries = [
                e for e in (info.get("entries") or []) if isinstance(e, dict)
            ]
            if not entries:
                raise RuntimeError("yt-dlp returned an empty playlist")
            info = entries[0]
        media_url = info.get("url") or _best_format_url(info) or url
        return ExtractedMedia(
            url=media_url,
            title=info.get("title"),
            ext=info.get("ext"),
            strategy_name=strategy,
        )

    def _via_opengraph(self, url: str, cookies: str | None) -> ExtractedMedia:
        page = self._fetch_page(url, cookies)
        media_url = None
        for prop in _OG_VIDEO_PROPERTIES:
            media_url = _meta_property(page, prop)
            if media_url:
                break
        if not media_url:
            raise RuntimeError("no og:video / twitter:player:stream meta tag found")
        return ExtractedMedia(
            url=media_url,
            title=_meta_property(page, "og:title"),
            ext=_guess_ext(media_url),
            strategy_name="opengraph",
        )

    def _via_oembed(self, url: str, cookies: str | None) -> ExtractedMedia:
        page = self._fetch_page(url, cookies)
        oembed_url = None
        for pattern in (
            r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/json\+oembed["\'][^>]+href=["\']([^"\']+)["\']',
            r'<link[^>]+type=["\']application/json\+oembed["\'][^>]+rel=["\']alternate["\'][^>]+href=["\']([^"\']+)["\']',
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/json\+oembed["\']',
        ):
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                oembed_url = html_module.unescape(match.group(1)).strip()
                break
        if not oembed_url:
            raise RuntimeError("no oEmbed discovery link found")
        try:
            import httpx

            resp = httpx.get(
                oembed_url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"oEmbed fetch failed: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("oEmbed response is not a JSON object")
        media_url = data.get("url") or _first_embedded_src(data.get("html") or "")
        if not media_url:
            raise RuntimeError("oEmbed response contains no media URL")
        return ExtractedMedia(
            url=media_url,
            title=data.get("title"),
            ext=_guess_ext(media_url),
            strategy_name="oembed",
        )

    def _via_video_tag(self, url: str, cookies: str | None) -> ExtractedMedia:
        page = self._fetch_page(url, cookies)
        for pattern in (
            r'<source[^>]+src=["\']([^"\']+)["\']',
            r'<video[^>]+src=["\']([^"\']+)["\']',
        ):
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                media_url = html_module.unescape(match.group(1)).strip()
                if media_url:
                    return ExtractedMedia(
                        url=media_url,
                        title=_meta_property(page, "og:title"),
                        ext=_guess_ext(media_url),
                        strategy_name="video_tag",
                    )
        raise RuntimeError("no <video>/<source> media URL found")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str, cookies: str | None) -> str:
        """GET a page; captcha walls raise CaptchaDetected, other HTTP
        failures raise RuntimeError (a failed attempt, not a captcha)."""
        import httpx

        cookie_dict = _netscape_to_cookie_dict(cookies) if cookies else None
        try:
            resp = httpx.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                cookies=cookie_dict,
                follow_redirects=True,
            )
        except Exception as exc:
            raise RuntimeError(f"page fetch failed: {exc}") from exc
        body = resp.text or ""
        if resp.status_code == 429 and is_captcha_error(body):
            raise CaptchaDetected(f"HTTP 429 with captcha page at {url!r}")
        if resp.status_code in (401, 403) and is_captcha_error(body):
            raise CaptchaDetected(f"HTTP {resp.status_code} bot-check at {url!r}")
        if is_captcha_error(body) and any(
            marker in body.lower()
            for marker in ("captcha", "recaptcha", "are you a robot")
        ):
            # A 200 page that IS a captcha interstitial (not a video page).
            raise CaptchaDetected(f"captcha interstitial at {url!r}")
        try:
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"page fetch failed: {exc}") from exc
        return body


def _best_format_url(info: dict) -> str | None:
    """Best-effort direct URL from a yt-dlp info dict's formats list."""
    formats = info.get("formats") or []
    for fmt in reversed(formats):  # yt-dlp sorts worst -> best
        if isinstance(fmt, dict) and fmt.get("url"):
            return fmt["url"]
    return None


def _first_embedded_src(html: str) -> str | None:
    """Pull the first <video>/<source>/<iframe> src out of oEmbed html."""
    for pattern in (
        r'<source[^>]+src=["\']([^"\']+)["\']',
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'<iframe[^>]+src=["\']([^"\']+)["\']',
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return html_module.unescape(match.group(1)).strip()
    return None


# ---------------------------------------------------------------------------
# Job-aware helpers
# ---------------------------------------------------------------------------


def mark_job_captcha(db: Session, job, detail: str = "") -> None:
    """Park a job on a captcha/bot-check wall.

    Sets captcha_required=True and last_error_code='E_CAPTCHA', then moves the
    job to RETRY_WAIT when the state machine allows it (a retryable state).
    When the current state has no RETRY_WAIT edge the status is left alone —
    the flag still records that cookies are needed. Commits.
    """
    job.captcha_required = True
    job.last_error_code = E_CAPTCHA
    try:
        validate_transition(job.status, RETRY_WAIT)
    except InvalidTransitionError:
        logger.warning(
            "extractor.captcha_no_retry_state",
            job_id=job.id,
            status=job.status,
        )
    else:
        job.status = RETRY_WAIT
    db.commit()
    logger.warning(
        "extractor.captcha_flagged", job_id=job.id, detail=(detail or "")[:200]
    )


def extract_media_for_job(
    db: Session,
    job,
    url: str,
    cookies: str | None = None,
    extractor: LinkExtractor | None = None,
) -> ExtractedMedia:
    """Run the fallback chain for a download job.

    On success the winning strategy is stored on job.extraction_strategy.
    On a captcha wall the job is parked via mark_job_captcha() and the
    ExtractionError is re-raised with captcha_detected=True.
    """
    ext = extractor or LinkExtractor()
    try:
        media = ext.extract(url, cookies)
    except ExtractionError as exc:
        if exc.captcha_detected:
            mark_job_captcha(db, job, f"extraction captcha for {url!r}")
        raise
    job.extraction_strategy = media.strategy_name
    db.commit()
    return media

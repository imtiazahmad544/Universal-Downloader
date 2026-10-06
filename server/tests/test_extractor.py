"""v2.0 extractor tests: fallback chain ordering, captcha detection, and the
job-aware extraction helpers (strategy recording + captcha parking)."""

from unittest.mock import Mock

import pytest

from app.services.extractor import (
    E_CAPTCHA,
    CaptchaDetected,
    ExtractedMedia,
    ExtractionError,
    extract_media_for_job,
    is_captcha_error,
    mark_job_captcha,
)
from app.services.state_machine import DOWNLOADING, READY, RETRY_WAIT
from tests.conftest import (
    make_customer,
    make_job,
    make_media,
    make_source,
    make_subscription,
)

URL = "https://example.com/watch?v=abc123"


def _media(strategy: str) -> ExtractedMedia:
    return ExtractedMedia(
        url="https://cdn.example.com/v.mp4",
        title="some video",
        ext="mp4",
        strategy_name=strategy,
    )


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


def test_first_strategy_wins():
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    ext._via_ytdlp = Mock(return_value=_media("ytdlp"))  # noqa: SLF001
    media = ext.extract(URL)
    assert media.strategy_name == "ytdlp"
    assert media.url == "https://cdn.example.com/v.mp4"


def test_fallback_third_strategy_hits_when_first_two_raise():
    """#1 (ytdlp) and #2 (ytdlp_cookies) raise -> #3 (opengraph) must win."""
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    ext._via_ytdlp = Mock(side_effect=RuntimeError("boom"))  # noqa: SLF001
    ext._via_opengraph = Mock(return_value=_media("opengraph"))  # noqa: SLF001
    ext._via_oembed = Mock(return_value=_media("oembed"))  # noqa: SLF001
    ext._via_video_tag = Mock(return_value=_media("video_tag"))  # noqa: SLF001

    media = ext.extract(URL, cookies="some\tcookies")

    assert media.strategy_name == "opengraph"
    # Both ytdlp variants were attempted (2 calls), then opengraph hit once.
    assert ext._via_ytdlp.call_count == 2  # noqa: SLF001
    assert ext._via_oembed.call_count == 0  # noqa: SLF001 — chain stopped
    assert ext._via_video_tag.call_count == 0  # noqa: SLF001


def test_cookies_strategy_skipped_without_cookies():
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    ext._via_ytdlp = Mock(side_effect=RuntimeError("nope"))  # noqa: SLF001
    ext._via_opengraph = Mock(return_value=_media("opengraph"))  # noqa: SLF001

    media = ext.extract(URL)  # cookies=None

    assert media.strategy_name == "opengraph"
    assert ext._via_ytdlp.call_count == 1  # noqa: SLF001 — cookies variant skipped


def test_all_strategies_fail_raises_with_attempts():
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    for name in (
        "_via_ytdlp",
        "_via_opengraph",
        "_via_oembed",
        "_via_video_tag",
    ):
        setattr(ext, name, Mock(side_effect=RuntimeError(f"{name} failed")))

    with pytest.raises(ExtractionError) as exc_info:
        ext.extract(URL, cookies="x")

    err = exc_info.value
    assert err.captcha_detected is False
    # 5 attempts: ytdlp, ytdlp_cookies, opengraph, oembed, video_tag
    assert len(err.attempts) == 5
    assert err.attempts[0][0] == "ytdlp"


def test_captcha_in_one_strategy_marks_error_but_chain_continues():
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    ext._via_ytdlp = Mock(side_effect=CaptchaDetected("sign in to confirm"))  # noqa: SLF001
    ext._via_opengraph = Mock(return_value=_media("opengraph"))  # noqa: SLF001

    # A later strategy can still win even after a captcha on an earlier one.
    media = ext.extract(URL)
    assert media.strategy_name == "opengraph"


def test_captcha_everywhere_raises_with_captcha_flag():
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    for name in (
        "_via_ytdlp",
        "_via_opengraph",
        "_via_oembed",
        "_via_video_tag",
    ):
        setattr(ext, name, Mock(side_effect=CaptchaDetected("captcha wall")))

    with pytest.raises(ExtractionError) as exc_info:
        ext.extract(URL, cookies="x")
    assert exc_info.value.captcha_detected is True


def test_extract_rejects_empty_url():
    from app.services.extractor import LinkExtractor

    with pytest.raises(ExtractionError):
        LinkExtractor().extract("")


# ---------------------------------------------------------------------------
# Captcha detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Sign in to confirm you're not a bot",
        "ERROR: Video unavailable: captcha required",
        "Please complete the reCAPTCHA challenge",
        "unusual traffic from your computer network",
        "verify you are human to continue",
    ],
)
def test_is_captcha_error_true(text):
    assert is_captcha_error(text) is True
    assert is_captcha_error(ValueError(text)) is True


@pytest.mark.parametrize(
    "text",
    [
        "HTTP Error 429: Too Many Requests",
        "connection timed out",
        "video not found",
        "",
    ],
)
def test_is_captcha_error_false(text):
    assert is_captcha_error(text) is False


# ---------------------------------------------------------------------------
# Job-aware helpers
# ---------------------------------------------------------------------------


def _downloading_job(db):
    customer = make_customer(db, code="EX1")
    source = make_source(db, customer)
    media = make_media(db, customer, source)
    return make_job(db, customer, media, status=DOWNLOADING)


def test_extract_media_for_job_records_winning_strategy(db):
    from app.services.extractor import LinkExtractor

    job = _downloading_job(db)
    ext = LinkExtractor()
    ext._via_ytdlp = Mock(return_value=_media("ytdlp_cookies"))  # noqa: SLF001

    media = extract_media_for_job(db, job, URL, cookies="x", extractor=ext)

    assert media.strategy_name == "ytdlp_cookies"
    db.expire_all()
    from app.models.models import DownloadJob

    assert db.get(DownloadJob, job.id).extraction_strategy == "ytdlp_cookies"


def test_extract_media_for_job_parks_job_on_captcha(db):
    from app.services.extractor import LinkExtractor

    job = _downloading_job(db)
    ext = LinkExtractor()
    ext._run_strategy = Mock(side_effect=CaptchaDetected("wall"))  # noqa: SLF001

    with pytest.raises(ExtractionError) as exc_info:
        extract_media_for_job(db, job, URL, extractor=ext)

    assert exc_info.value.captcha_detected is True
    db.expire_all()
    from app.models.models import DownloadJob

    parked = db.get(DownloadJob, job.id)
    assert parked.captcha_required is True
    assert parked.last_error_code == E_CAPTCHA
    # DOWNLOADING -> RETRY_WAIT is legal: the job stays retryable.
    assert parked.status == RETRY_WAIT


def test_mark_job_captcha_keeps_unrelated_state(db):
    """When the current state has no RETRY_WAIT edge, the flag is still set
    but the status is left untouched (never force an illegal transition)."""
    customer = make_customer(db, code="EX2")
    source = make_source(db, customer)
    media = make_media(db, customer, source)
    job = make_job(db, customer, media, status=READY)

    mark_job_captcha(db, job, "captcha during prefetch")

    db.expire_all()
    from app.models.models import DownloadJob

    parked = db.get(DownloadJob, job.id)
    assert parked.captcha_required is True
    assert parked.last_error_code == E_CAPTCHA
    # READY has no RETRY_WAIT edge -> status untouched.
    assert parked.status == READY


def test_extract_media_for_job_non_captcha_failure_does_not_flag(db):
    from app.services.extractor import LinkExtractor

    job = _downloading_job(db)
    ext = LinkExtractor()
    ext._run_strategy = Mock(side_effect=RuntimeError("plain failure"))  # noqa: SLF001

    with pytest.raises(ExtractionError) as exc_info:
        extract_media_for_job(db, job, URL, extractor=ext)

    assert exc_info.value.captcha_detected is False
    db.expire_all()
    from app.models.models import DownloadJob

    untouched = db.get(DownloadJob, job.id)
    assert untouched.captcha_required is False
    assert untouched.status == DOWNLOADING


def test_ytdlp_strategy_reports_missing_dependency():
    """yt-dlp is not installed in the test env: strategies 1-2 fail as
    ordinary attempts and the chain keeps moving (never ImportError out)."""
    import importlib.util

    assert importlib.util.find_spec("yt_dlp") is None
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    with pytest.raises(RuntimeError, match="yt-dlp not installed"):
        ext._via_ytdlp(URL, None)  # noqa: SLF001

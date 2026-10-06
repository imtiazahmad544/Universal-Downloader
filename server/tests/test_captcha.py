"""v2.0 captcha flow tests: extract -> park (E_CAPTCHA) -> upload cookies ->
resume clears the flag and re-arms the job; the /extract diagnostic endpoint.
"""

from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

import app.workers.discovery as discovery_mod
from app.models.models import (
    EVENT_CAPTCHA,
    AuditLog,
    DiscoveryRun,
    DownloadJob,
    ProviderEvent,
)
from app.providers.base import DiscoveryResult, ProviderResultType
from app.services.extractor import (
    E_CAPTCHA,
    CaptchaDetected,
    ExtractedMedia,
    ExtractionError,
    extract_media_for_job,
)
from app.services.state_machine import DOWNLOADING, READY, RETRY_WAIT
from tests.conftest import (
    make_customer,
    make_job,
    make_media,
    make_source,
    make_stack,
)
from tests.test_cookies import VALID_TEXT as COOKIES_TEXT
from tests.test_cookies import _upload


@pytest.fixture
def _patched_worker_session(engine, monkeypatch):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(discovery_mod, "SessionLocal", factory)
    return factory


def _stack_downloading_job(db, client, code):
    """A downloading job owned by a full stack (customer+user+subscription)."""
    stack = make_stack(db, client, code=code)
    source = make_source(db, stack.customer)
    media = make_media(db, stack.customer, source)
    job = make_job(db, stack.customer, media, status=DOWNLOADING)
    return stack, source, job


def _captcha_extractor():
    from app.services.extractor import LinkExtractor

    ext = LinkExtractor()
    ext._run_strategy = Mock(side_effect=CaptchaDetected("sign in to confirm"))  # noqa: SLF001
    return ext


# ---------------------------------------------------------------------------
# extract -> park flow
# ---------------------------------------------------------------------------


def test_extract_captcha_parks_job_retryable(db):
    customer = make_customer(db, code="CAP1")
    source = make_source(db, customer)
    media = make_media(db, customer, source)
    job = make_job(db, customer, media, status=DOWNLOADING)

    with pytest.raises(ExtractionError):
        extract_media_for_job(
            db, job, "https://example.com/v/1", extractor=_captcha_extractor()
        )

    db.expire_all()
    parked = db.get(DownloadJob, job.id)
    assert parked.captcha_required is True
    assert parked.last_error_code == E_CAPTCHA
    assert parked.status == RETRY_WAIT  # DOWNLOADING -> RETRY_WAIT is legal


def test_resume_clears_captcha_and_rearms(db, client):
    stack, source, job = _stack_downloading_job(db, client, code="CAP2U")

    with pytest.raises(ExtractionError):
        extract_media_for_job(
            db, job, "https://example.com/v/1", extractor=_captcha_extractor()
        )

    # Upload cookies first (the documented order).
    resp = _upload(client, "/api/v1/me/cookies", COOKIES_TEXT, stack.headers)
    assert resp.status_code == 200, resp.text

    # Resume clears the flag and re-arms to READY.
    resp = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["captcha_required"] is False
    assert body["status"] == READY
    # last_error_code is kept as history.
    assert body["last_error_code"] == E_CAPTCHA

    # The audit trail records the captcha clearance + cookies availability.
    db.expire_all()
    resumed = (
        db.query(AuditLog)
        .filter(AuditLog.action == "job.resumed", AuditLog.entity_id == str(job.id))
        .one()
    )
    assert resumed.meta["captcha_cleared"] is True
    assert resumed.meta["cookies_available"] is True


def test_resume_captcha_without_cookies_still_clears(db, client):
    """Resume is not blocked when no cookies exist (the wall may have been
    transient); the audit trail records cookies_available=False."""
    stack, source, job = _stack_downloading_job(db, client, code="CAP3U")

    with pytest.raises(ExtractionError):
        extract_media_for_job(
            db, job, "https://example.com/v/1", extractor=_captcha_extractor()
        )

    resp = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["captcha_required"] is False
    assert resp.json()["status"] == READY

    db.expire_all()
    resumed = (
        db.query(AuditLog)
        .filter(AuditLog.action == "job.resumed", AuditLog.entity_id == str(job.id))
        .one()
    )
    assert resumed.meta["cookies_available"] is False


def test_resume_captcha_rejected_from_wrong_state(db, client):
    stack, source, job = _stack_downloading_job(db, client, code="CAP4U")
    job.captcha_required = True  # flagged but still DOWNLOADING (not retryable)
    db.commit()

    resp = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resp.status_code == 409


def test_resume_non_captcha_job_unchanged(db, client):
    """The classic PAUSED -> resume path still works alongside the captcha
    flow."""
    stack = make_stack(db, client, code="CAP5")
    source = make_source(db, stack.customer)
    media = make_media(db, stack.customer, source)
    job = make_job(db, stack.customer, media, status="paused")

    resp = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"  # unbatched paused -> QUEUED
    assert resp.json()["captcha_required"] is False


def test_job_read_surfaces_captcha_fields(db, client):
    stack = make_stack(db, client, code="CAP6")
    source = make_source(db, stack.customer)
    media = make_media(db, stack.customer, source)
    job = make_job(db, stack.customer, media, status="retry_wait")
    job.captcha_required = True
    job.last_error_code = E_CAPTCHA
    job.extraction_strategy = "ytdlp"
    db.commit()

    resp = client.get("/api/v1/jobs/", headers=stack.headers)
    assert resp.status_code == 200
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["captcha_required"] is True
    assert row["last_error_code"] == E_CAPTCHA
    assert row["extraction_strategy"] == "ytdlp"


# ---------------------------------------------------------------------------
# POST /api/v1/extract
# ---------------------------------------------------------------------------


def test_extract_endpoint_success(db, client, monkeypatch):
    from app.api.v1 import extract as extract_mod

    stack = make_stack(db, client, code="CAP10")
    monkeypatch.setattr(
        extract_mod.LinkExtractor,
        "extract",
        lambda self, url, cookies=None: ExtractedMedia(
            url="https://cdn.example.com/v.mp4",
            title="vid",
            ext="mp4",
            strategy_name="opengraph",
        ),
    )
    resp = client.post(
        "/api/v1/extract", json={"url": "https://example.com/v"}, headers=stack.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "ok": True,
        "media_url": "https://cdn.example.com/v.mp4",
        "title": "vid",
        "ext": "mp4",
        "strategy": "opengraph",
        "error": None,
        "captcha_required": False,
    }


def test_extract_endpoint_captcha(db, client, monkeypatch):
    from app.api.v1 import extract as extract_mod

    stack = make_stack(db, client, code="CAP11")

    def _boom(self, url, cookies=None):
        raise ExtractionError(
            url, [("ytdlp", "captcha: sign in to confirm")], captcha_detected=True
        )

    monkeypatch.setattr(extract_mod.LinkExtractor, "extract", _boom)
    resp = client.post(
        "/api/v1/extract", json={"url": "https://example.com/v"}, headers=stack.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["captcha_required"] is True
    assert body["error"]


def test_extract_endpoint_requires_auth(client):
    resp = client.post("/api/v1/extract", json={"url": "https://example.com/v"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Discovery worker: captcha classification
# ---------------------------------------------------------------------------


class _FakeCaptchaProvider:
    name = "fake-captcha"
    supported_platforms = {"youtube"}

    async def discover(self, source, cookies=None):
        return DiscoveryResult(
            ProviderResultType.CAPTCHA, error="captcha/bot-check detected: sign in"
        )


def test_discovery_captcha_fails_run_with_event(
    db, _patched_worker_session, monkeypatch
):
    customer = make_customer(db, code="CAP20")
    source = make_source(db, customer, platform="youtube", input_value="@somehandle")
    run = DiscoveryRun(source_id=source.id, provider="auto", status="pending")
    db.add(run)
    db.commit()

    monkeypatch.setattr(
        discovery_mod, "get_discovery_providers", lambda: [_FakeCaptchaProvider()]
    )
    import asyncio

    asyncio.run(discovery_mod.process(run.id))

    db.expire_all()
    failed = db.get(DiscoveryRun, run.id)
    assert failed.status == "failed"
    assert "captcha" in (failed.error or "").lower()
    events = (
        db.query(ProviderEvent)
        .filter(ProviderEvent.event_type == EVENT_CAPTCHA)
        .all()
    )
    assert len(events) == 1
    assert events[0].provider == "fake-captcha"


def test_discovery_passes_cookies_to_provider(
    db, _patched_worker_session, monkeypatch
):
    """The worker resolves the customer's cookies and hands them to the
    provider's discover() call."""
    from app.services import cookies as cookies_mod
    from cryptography.fernet import Fernet

    customer = make_customer(db, code="CAP21")
    monkeypatch.setattr(
        cookies_mod.settings,
        "COOKIES_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    from app.services.cookies import store_customer_cookies

    store_customer_cookies(db, customer, COOKIES_TEXT)
    db.commit()
    source = make_source(db, customer, platform="youtube", input_value="@somehandle")
    run = DiscoveryRun(source_id=source.id, provider="auto", status="pending")
    db.add(run)
    db.commit()

    seen = {}

    class _SpyProvider:
        name = "spy"
        supported_platforms = {"youtube"}

        async def discover(self, source, cookies=None):
            seen["cookies"] = cookies
            from app.providers.base import DiscoveredMedia

            return DiscoveryResult(
                ProviderResultType.SUCCESS,
                items=[DiscoveredMedia(url="https://example.com/v/1")],
            )

    monkeypatch.setattr(discovery_mod, "get_discovery_providers", lambda: [_SpyProvider()])
    import asyncio

    asyncio.run(discovery_mod.process(run.id))

    assert seen.get("cookies") == COOKIES_TEXT

"""Provider contract tests: backoff math, discovery-worker fallback policy,
rate-limit behavior, and adapter unsupported-reporting (yt-dlp not installed)."""

import importlib.util
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

import app.workers.discovery as discovery_mod
from app.models.models import AuditLog, DiscoveryRun, MediaItem, ProviderEvent
from app.providers.base import (
    DiscoveredMedia,
    DiscoveryResult,
    ProviderResultType,
    compute_backoff,
)
from app.providers.instagram import InstagramDiscoveryProvider
from app.providers.tiktok import TikTokDiscoveryProvider
from app.providers.youtube import YouTubeDiscoveryProvider
from app.services.audit import PROVIDER_FALLBACK
from tests.conftest import make_customer, make_source


# ---------------------------------------------------------------------------
# compute_backoff
# ---------------------------------------------------------------------------


def test_compute_backoff():
    # compute_backoff(attempt) with defaults base=2.0, cap=300.0
    assert compute_backoff(0) == 2.0
    assert compute_backoff(1) == 4.0
    assert compute_backoff(3) == 16.0
    assert compute_backoff(10) == 300.0  # 2*2**10 = 2048 -> capped at 300
    assert compute_backoff(100) == 300.0  # stays capped


def test_compute_backoff_custom_base():
    assert compute_backoff(2, base=10.0) == 40.0


# ---------------------------------------------------------------------------
# Fakes + fixtures for driving the discovery worker's provider loop
# ---------------------------------------------------------------------------


class _FakeUnsupported:
    name = "fake-unsupported"
    supported_platforms = {"youtube"}

    def __init__(self):
        self.calls = 0

    async def discover(self, source):
        self.calls += 1
        return DiscoveryResult(ProviderResultType.UNSUPPORTED, error="not me")


class _FakeSuccess:
    name = "fake-success"
    supported_platforms = {"youtube"}

    def __init__(self, items):
        self.calls = 0
        self.items = items

    async def discover(self, source):
        self.calls += 1
        return DiscoveryResult(ProviderResultType.SUCCESS, items=self.items)


class _FakeRateLimited:
    name = "fake-rate-limited"
    supported_platforms = {"youtube"}

    def __init__(self, retry_after=45.0):
        self.calls = 0
        self.retry_after = retry_after

    async def discover(self, source):
        self.calls += 1
        return DiscoveryResult(
            ProviderResultType.RATE_LIMITED,
            error="slow down",
            retry_after=self.retry_after,
        )


@pytest.fixture
def _patched_worker_session(engine, monkeypatch):
    """Point the worker's module-global SessionLocal at the test engine.

    The discovery worker builds its own sessions internally (it does not go
    through the FastAPI dependency), so the registry test must rebind it.
    """
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(discovery_mod, "SessionLocal", factory)
    return factory


@pytest.fixture
def yt_run(db):
    customer = make_customer(db, code="YT1")
    source = make_source(db, customer, platform="youtube", input_value="@somehandle")
    run = DiscoveryRun(source_id=source.id, provider="auto", status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"customer": customer, "source": source, "run": run}


# ---------------------------------------------------------------------------
# Fallback selection through the worker's provider loop
# ---------------------------------------------------------------------------


async def test_fallback_unsupported_then_success(
    db, yt_run, _patched_worker_session, monkeypatch
):
    items = [
        DiscoveredMedia(url="https://example.com/v/1", external_id="v1", title="one"),
        DiscoveredMedia(url="https://example.com/v/2", external_id="v2", title="two"),
    ]
    first = _FakeUnsupported()
    second = _FakeSuccess(items)
    monkeypatch.setattr(
        discovery_mod, "get_discovery_providers", lambda: [first, second]
    )

    await discovery_mod.process(yt_run["run"].id)

    assert first.calls == 1
    assert second.calls == 1  # fell through on UNSUPPORTED, then stopped on SUCCESS
    db.expire_all()
    run = db.get(DiscoveryRun, yt_run["run"].id)
    assert run.status == "success"
    assert run.items_found == 2
    assert (
        db.query(MediaItem)
        .filter(MediaItem.customer_id == yt_run["customer"].id)
        .count()
        == 2
    )
    # The fallback was audited.
    fallbacks = db.query(AuditLog).filter(AuditLog.action == PROVIDER_FALLBACK).all()
    assert len(fallbacks) == 1
    assert fallbacks[0].meta["from"] == "fake-unsupported"


async def test_rate_limited_fails_run_with_no_rotation(
    db, yt_run, _patched_worker_session, monkeypatch
):
    rate_limited = _FakeRateLimited(retry_after=45.0)
    healthy = _FakeSuccess(
        [DiscoveredMedia(url="https://example.com/v/9", external_id="v9")]
    )
    monkeypatch.setattr(
        discovery_mod, "get_discovery_providers", lambda: [rate_limited, healthy]
    )

    await discovery_mod.process(yt_run["run"].id)

    assert rate_limited.calls == 1
    # NEVER rotate providers to dodge rate limits: the healthy provider is
    # not consulted and no media is persisted.
    assert healthy.calls == 0
    assert (
        db.query(MediaItem)
        .filter(MediaItem.customer_id == yt_run["customer"].id)
        .count()
        == 0
    )
    db.expire_all()
    run = db.get(DiscoveryRun, yt_run["run"].id)
    assert run.status == "failed"
    assert "rate limited" in (run.error or "")
    events = (
        db.query(ProviderEvent)
        .filter(ProviderEvent.event_type == "rate_limited")
        .all()
    )
    assert len(events) == 1
    assert events[0].provider == "fake-rate-limited"
    assert events[0].details["retry_after"] == 45.0


# ---------------------------------------------------------------------------
# Real adapters: unsupported reporting without optional dependencies
# ---------------------------------------------------------------------------


async def test_youtube_adapter_unsupported_without_ytdlp():
    assert importlib.util.find_spec("yt_dlp") is None  # do NOT install yt-dlp
    result = await YouTubeDiscoveryProvider().discover(
        SimpleNamespace(input_value="@somehandle")
    )
    assert result.result_type == ProviderResultType.UNSUPPORTED
    assert "yt-dlp" in (result.error or "")


async def test_tiktok_stub_unsupported_with_credential_message():
    result = await TikTokDiscoveryProvider().discover(
        SimpleNamespace(input_value="@someuser")
    )
    assert result.result_type == ProviderResultType.UNSUPPORTED
    assert "TIKTOK_API_KEY" in (result.error or "")


async def test_instagram_stub_unsupported_with_credential_message():
    result = await InstagramDiscoveryProvider().discover(
        SimpleNamespace(input_value="@someuser")
    )
    assert result.result_type == ProviderResultType.UNSUPPORTED
    assert "INSTAGRAM_API_TOKEN" in (result.error or "")

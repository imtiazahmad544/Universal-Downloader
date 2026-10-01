"""Discovery worker.

Polls the database for DiscoveryRun rows in 'pending' status, claims them
with a race-safe conditional UPDATE (status='pending' -> 'running',
rowcount==1 wins), then drives provider discovery with fallback across
the ordered provider registry.

Rate-limit policy: when a provider reports RATE_LIMITED the run fails
immediately — providers are NEVER rotated to dodge rate limits.

Redis is OPTIONAL and best-effort only: maybe_notify_redis() is a no-op-safe
hook called from process(); the database remains the source of truth and
polling works with no Redis configured at all (graceful DB fallback).

Run with:  python -m app.workers.discovery   (from server/)
"""

import asyncio
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.core.canonicalize import canonicalize_url
from app.core.config import settings
from app.core.database import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.models import (
    DISCOVERY_FAILED,
    DISCOVERY_PARTIAL,
    DISCOVERY_PENDING,
    DISCOVERY_RUNNING,
    DISCOVERY_SUCCESS,
    EVENT_ERROR,
    EVENT_RATE_LIMITED,
    MEDIA_DISCOVERED,
    SOURCE_REMOVED,
    DiscoveryRun,
    DownloadJob,
    MediaItem,
    ProviderEvent,
    Source,
)
from app.providers import get_discovery_providers
from app.providers.base import (
    DiscoveryResult,
    ProviderResultType,
    compute_backoff,
    DiscoveryProvider,
)
from app.services.audit import (
    DISCOVERY_COMPLETED,
    PROVIDER_FALLBACK,
    PROVIDER_SELECTED,
    log_event,
)
from app.services.state_machine import DISCOVERED as JOB_DISCOVERED

logger = get_logger("universal-downloader.workers.discovery")

_CLAIM_LIMIT = 5


# ---------------------------------------------------------------------------
# Optional Redis (best-effort, no-op-safe)
# ---------------------------------------------------------------------------


def maybe_notify_redis(run_id: int) -> None:
    """Best-effort Redis hook: no-op when REDIS_URL is blank, the `redis`
    package is missing, or the server is unreachable.

    The database is the source of truth for discovery runs — the worker polls
    regardless. This hook exists so the API workstream (or a future queue
    consumer) can optionally publish/subscribe on 'discovery:pending'; a
    failure here never affects run processing.
    """
    if not settings.REDIS_URL:
        return
    try:
        import redis
    except ImportError:
        logger.debug("discovery.redis_unavailable", reason="redis package missing")
        return
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        try:
            client.rpush("discovery:pending", run_id)
        finally:
            client.close()
    except Exception:
        logger.debug("discovery.redis_unavailable", reason="connection failed")


# ---------------------------------------------------------------------------
# Claiming (race-safe conditional UPDATE)
# ---------------------------------------------------------------------------


def claim_pending_runs(limit: int = _CLAIM_LIMIT) -> list[int]:
    """Claim up to `limit` pending runs. Only the worker whose UPDATE flips
    exactly one row wins; losers roll back and skip (race-safe across
    multiple worker processes)."""
    db: Session = SessionLocal()
    claimed: list[int] = []
    try:
        candidates = (
            db.query(DiscoveryRun.id)
            .filter(DiscoveryRun.status == DISCOVERY_PENDING)
            .order_by(DiscoveryRun.id.asc())
            .limit(limit)
            .all()
        )
        for (run_id,) in candidates:
            updated = (
                db.query(DiscoveryRun)
                .filter(
                    DiscoveryRun.id == run_id,
                    DiscoveryRun.status == DISCOVERY_PENDING,
                )
                .update(
                    {"status": DISCOVERY_RUNNING, "started_at": utcnow()},
                    synchronize_session=False,
                )
            )
            if updated == 1:
                db.commit()
                claimed.append(run_id)
                logger.info("discovery.claimed", run_id=run_id)
            else:
                db.rollback()  # another worker won the race; skip
        return claimed
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Sync persistence helpers (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _fail_run(run_id: int, error: str) -> None:
    db: Session = SessionLocal()
    try:
        db.query(DiscoveryRun).filter(DiscoveryRun.id == run_id).update(
            {
                "status": DISCOVERY_FAILED,
                "error": error[:1024],
                "completed_at": utcnow(),
            },
            synchronize_session=False,
        )
        db.commit()
        logger.info("discovery.run_failed", run_id=run_id, error=error[:200])
    finally:
        db.close()


def _audit(db: Session, run_id: int, source_id: int, action: str, meta: dict) -> None:
    log_event(
        db,
        actor_id=None,
        action=action,
        entity_type="discovery_run",
        entity_id=str(run_id),
        meta={"run_id": run_id, "source_id": source_id, **meta},
    )


def _provider_event(
    run_id: int, provider_name: str, event_type: str, details: dict
) -> None:
    db: Session = SessionLocal()
    try:
        db.add(
            ProviderEvent(
                job_id=None,
                discovery_run_id=run_id,
                provider=provider_name,
                event_type=event_type,
                details=details,
            )
        )
        db.commit()
    finally:
        db.close()


def _persist_success(
    run_id: int,
    customer_id: int,
    source_id: int,
    items: list,
) -> None:
    """Persist discovery items with canonical-URL dedup.

    - canonicalize_url() failure -> invalid (counted, skipped)
    - existing (customer_id, canonical_url) -> duplicate (counted, skipped)
    - otherwise a MediaItem(status='discovered') + DownloadJob(status='discovered')
    """
    cap = settings.DISCOVERY_MAX_ITEMS
    db: Session = SessionLocal()
    try:
        new = dup = invalid = 0
        for item in items[:cap]:
            raw_url = (item.url or "").strip()
            try:
                canonical_url = canonicalize_url(raw_url)
            except ValueError:
                invalid += 1
                continue
            if not canonical_url:
                invalid += 1
                continue
            exists = (
                db.query(MediaItem.id)
                .filter(
                    MediaItem.customer_id == customer_id,
                    MediaItem.canonical_url == canonical_url,
                )
                .first()
            )
            if exists is not None:
                # Duplicate: counted, not re-created, and deliberately no
                # ProviderEvent — that would spam one row per duplicate.
                dup += 1
                continue
            media = MediaItem(
                customer_id=customer_id,
                source_id=source_id,
                canonical_url=canonical_url,
                external_id=item.external_id,
                status=MEDIA_DISCOVERED,
            )
            db.add(media)
            db.flush()  # need media.id for the job row
            db.add(
                DownloadJob(
                    customer_id=customer_id,
                    media_item_id=media.id,
                    status=JOB_DISCOVERED,
                )
            )
            new += 1

        run = db.get(DiscoveryRun, run_id)
        if run is not None:
            run.items_found = new
            run.status = DISCOVERY_SUCCESS if invalid == 0 else DISCOVERY_PARTIAL
            run.completed_at = utcnow()

        _audit(
            db,
            run_id,
            source_id,
            DISCOVERY_COMPLETED,
            {"new": new, "duplicate": dup, "invalid": invalid},
        )
        db.commit()
        logger.info(
            "discovery.run_complete",
            run_id=run_id,
            new=new,
            duplicate=dup,
            invalid=invalid,
        )
    finally:
        db.close()


def _load_context(run_id: int) -> dict | None:
    """Load run + source attributes needed by process(); None if run missing."""
    db: Session = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return None
        source = db.get(Source, run.source_id)
        if source is None:
            return None
        return {
            "run_id": run.id,
            "source_id": source.id,
            "source_status": source.status,
            "platform": source.platform,
            "customer_id": source.customer_id,
            "source_proxy": SimpleNamespace(
                id=source.id,
                platform=source.platform,
                input_value=source.input_value,
                customer_id=source.customer_id,
            ),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Run processing
# ---------------------------------------------------------------------------


async def _handle_result(
    ctx: dict, provider: DiscoveryProvider, result: DiscoveryResult
) -> str:
    """Handle one provider's DiscoveryResult. Returns 'done' when the run is
    finished (break the provider loop) or 'continue' to try the next provider
    (UNSUPPORTED fallback only)."""
    run_id = ctx["run_id"]
    source_id = ctx["source_id"]
    rt = result.result_type

    if rt == ProviderResultType.SUCCESS:
        await asyncio.to_thread(
            _persist_success, run_id, ctx["customer_id"], source_id, result.items
        )
        return "done"

    if rt == ProviderResultType.UNSUPPORTED:
        db = SessionLocal()
        try:
            _audit(
                db,
                run_id,
                source_id,
                PROVIDER_FALLBACK,
                {"from": provider.name, "reason": result.error},
            )
        finally:
            db.close()
        logger.info(
            "discovery.provider_unsupported",
            run_id=run_id,
            provider=provider.name,
            reason=(result.error or "")[:200],
        )
        return "continue"

    if rt == ProviderResultType.RETRYABLE_ERROR:
        await asyncio.to_thread(
            _provider_event,
            run_id,
            provider.name,
            EVENT_ERROR,
            {"error": result.error, "backoff_s": compute_backoff(0)},
        )
        await asyncio.to_thread(_fail_run, run_id, result.error or "retryable error")
        return "done"

    if rt == ProviderResultType.RATE_LIMITED:
        # NEVER rotate providers to dodge rate limits: fail the run and let a
        # later poll retry after the provider-specified backoff.
        await asyncio.to_thread(
            _provider_event,
            run_id,
            provider.name,
            EVENT_RATE_LIMITED,
            {"retry_after": result.retry_after},
        )
        await asyncio.to_thread(
            _fail_run, run_id, f"rate limited; retry_after={result.retry_after}"
        )
        return "done"

    # NON_RETRYABLE_ERROR (and any unknown result type: fail closed)
    await asyncio.to_thread(
        _fail_run, run_id, result.error or "non-retryable provider error"
    )
    return "done"


async def process(run_id: int) -> None:
    """Process one claimed discovery run through the provider registry."""
    maybe_notify_redis(run_id)  # no-op-safe hook; DB polling is authoritative

    ctx = await asyncio.to_thread(_load_context, run_id)
    if ctx is None:
        logger.warning("discovery.run_missing", run_id=run_id)
        return

    if ctx["source_status"] == SOURCE_REMOVED:
        await asyncio.to_thread(_fail_run, run_id, "source removed")
        return

    registry = get_discovery_providers()
    tried: list[str] = []
    for provider in registry:
        if ctx["platform"] not in provider.supported_platforms:
            continue
        db = SessionLocal()
        try:
            _audit(
                db,
                run_id,
                ctx["source_id"],
                PROVIDER_SELECTED,
                {"provider": provider.name, "source_id": ctx["source_id"]},
            )
        finally:
            db.close()
        logger.info(
            "discovery.provider_selected",
            run_id=run_id,
            provider=provider.name,
        )
        result = await provider.discover(ctx["source_proxy"])
        outcome = await _handle_result(ctx, provider, result)
        if outcome == "continue":
            tried.append(provider.name)
            continue
        return

    # Every matching provider reported UNSUPPORTED (or none matched).
    await asyncio.to_thread(_fail_run, run_id, "no supported provider")


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


async def main() -> None:
    """Loop forever until cancelled: claim pending runs, process each, sleep."""
    configure_logging()
    logger.info(
        "discovery.worker_started",
        poll_seconds=settings.DISCOVERY_POLL_SECONDS,
    )
    try:
        while True:
            try:
                claimed = await asyncio.to_thread(claim_pending_runs)
                for run_id in claimed:
                    try:
                        await process(run_id)
                    except Exception:
                        logger.exception("discovery.process_failed", run_id=run_id)
                        await asyncio.to_thread(
                            _fail_run, run_id, "worker exception during processing"
                        )
            except Exception:
                logger.exception("discovery.poll_failed")
            await asyncio.sleep(settings.DISCOVERY_POLL_SECONDS)
    except asyncio.CancelledError:
        logger.info("discovery.worker_stopping")
        raise


if __name__ == "__main__":
    asyncio.run(main())

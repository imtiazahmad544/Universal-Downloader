"""Batch scheduler: assign queued jobs to per-customer daily batches.

v2.0 window semantics:
    Each customer has a daily batch WINDOW that opens at
    customer.daily_start_time ("HH:MM") in the customer's timezone
    (default "00:00", which reproduces the v1.1 midnight behavior exactly).
    The batch date is the date of the most recent window opening — the
    cutover happens at the customer's start time, not at midnight.

Window-cutover rule (structural, not conditional):
    Eligibility for batching is `status == 'QUEUED'` AND `batch_id IS NULL`.
    A job that has moved beyond QUEUED (e.g. BATCHED/READY/DOWNLOADING)
    always keeps its originating batch, even when the scheduler ticks past
    the customer's window opening. The batch date is computed from the
    customer's local time at assignment time only; it is never recomputed
    or "rolled over" for jobs that already have a batch_id. Because
    run_scheduler_for_customer() is idempotent per (customer_id, batch_date)
    via the uq_batches_customer_date constraint, two scheduler ticks in the
    same window always return the same batch.

Thundering-herd protection (v2.0):
    compute_next_run() adds +/- JITTER_SECONDS of jitter to each customer's
    next window opening, so customers sharing a start time (e.g. the 00:00
    default) do not all stampede in the same worker tick.

Missed-window catch-up (v2.0):
    customers.last_scheduler_run_at records the last completed pass (naive
    UTC). When it is missing or older than CATCHUP_AFTER (25h), the next
    window is considered due immediately — the worker runs the pass now
    instead of waiting for the next opening.
"""

import random
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import utcnow
from app.models.models import (
    BATCH_ACTIVE,
    BATCH_OPEN,
    SOURCE_ACTIVE,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_EXPIRING_SOON,
    Batch,
    Customer,
    DownloadJob,
    MediaItem,
    Source,
)
from app.services.audit import BATCH_CREATED, JOB_STATUS_CHANGED, log_event
from app.services.state_machine import (
    BATCHED,
    QUEUED,
    InvalidTransitionError,
    validate_transition,
)
from app.services.subscriptions import effective_status, get_subscription

logger = structlog.get_logger("universal-downloader.scheduler")

# Jobs in this status are eligible for batch assignment. Jobs that already
# carry a batch_id (BATCHED or later) keep their originating batch forever —
# the window-cutover rule is structural. Using the state-machine constants
# keeps the scheduler in lockstep with the central transition graph.
_ELIGIBLE_JOB_STATUS = QUEUED

# +/- jitter applied to each customer's next window opening.
JITTER_SECONDS = 300

# A customer with no successful scheduler pass in this long is due
# immediately (missed-window catch-up).
CATCHUP_AFTER = timedelta(hours=25)

_START_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

DEFAULT_START_TIME = "00:00"


def validate_daily_start_time(value: str) -> tuple[int, int]:
    """Validate a daily start time. Returns (hour, minute).

    Raises ValueError unless `value` is "HH:MM" in 24-hour format
    ("00:00".."23:59").
    """
    match = _START_TIME_RE.match((value or "").strip())
    if not match:
        raise ValueError(
            f"invalid daily_start_time {value!r}: expected 'HH:MM' 24-hour format"
        )
    return int(match.group(1)), int(match.group(2))


def _safe_start_time(value: str | None) -> tuple[int, int]:
    """Parse a stored start time, falling back to 00:00 on garbage."""
    try:
        return validate_daily_start_time(value or "")
    except ValueError:
        logger.warning(
            "scheduler.invalid_start_time_fallback",
            value=value,
            fallback=DEFAULT_START_TIME,
        )
        return (0, 0)


def _zoneinfo_or_utc(tz_name: str | None) -> ZoneInfo:
    """ZoneInfo with UTC fallback for unknown/invalid names."""
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("UTC")


def local_batch_info(tz_name: str, now_utc_naive: datetime) -> tuple[date, datetime]:
    """Convert a naive-UTC ``now`` into the customer's local batch date.

    Returns ``(local_batch_date, next_local_midnight_as_aware_utc)``.
    Unknown/invalid timezone names fall back to UTC. Pure helper (no DB).

    NOTE (v2.0): kept for backward compatibility. New code should use
    window_date_for(), which generalizes the midnight cutover to the
    customer's daily_start_time (identical when the start time is "00:00").
    """
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        tz = ZoneInfo("UTC")
    aware_utc = now_utc_naive.replace(tzinfo=timezone.utc)
    local_now = aware_utc.astimezone(tz)
    batch_date = local_now.date()
    next_midnight_local = (local_now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return batch_date, next_midnight_local.astimezone(timezone.utc)


def window_date_for(
    tz_name: str | None, daily_start_time: str | None, now_utc_naive: datetime
) -> date:
    """Date of the most recent daily window opening at/under ``now``.

    The window opens every day at daily_start_time in the customer's
    timezone. With "00:00" this is exactly the local calendar date (v1.1
    behavior); with e.g. "06:00", the cutover happens at 06:00 local, so
    03:00 local still belongs to yesterday's window. Pure helper (no DB).
    """
    tz = _zoneinfo_or_utc(tz_name)
    hour, minute = _safe_start_time(daily_start_time)
    local_now = now_utc_naive.replace(tzinfo=timezone.utc).astimezone(tz)
    opening_today = local_now.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if local_now >= opening_today:
        return local_now.date()
    return (local_now - timedelta(days=1)).date()


def compute_next_run(
    tz_name: str | None,
    daily_start_time: str | None,
    now_utc_naive: datetime,
    last_run_utc_naive: datetime | None = None,
    jitter_seconds: float | None = None,
) -> datetime:
    """Compute the next due time for a customer's scheduler pass.

    Returns an aware-UTC datetime. The customer is due NOW (catch-up) when:
    - last_run is missing (first ever run), or
    - no successful run in the last CATCHUP_AFTER (25h), or
    - the current window opened after the last run (its batch was never
      built — e.g. the worker was down across the opening).

    Otherwise the pass is due at the NEXT window opening at daily_start_time
    in the customer's timezone, plus +/- jitter (JITTER_SECONDS when
    jitter_seconds is None; pass an explicit value — e.g. 0 — for
    deterministic tests).

    Pure helper (no DB).
    """
    now_aware = now_utc_naive.replace(tzinfo=timezone.utc)
    tz = _zoneinfo_or_utc(tz_name)
    hour, minute = _safe_start_time(daily_start_time)
    local_now = now_aware.astimezone(tz)
    opening_today = local_now.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    most_recent_opening = (
        opening_today
        if local_now >= opening_today
        else opening_today - timedelta(days=1)
    )
    if last_run_utc_naive is None:
        return now_aware  # first ever run
    if (now_utc_naive - last_run_utc_naive) > CATCHUP_AFTER:
        return now_aware  # missed-window catch-up: no run in 25h
    if last_run_utc_naive.replace(tzinfo=timezone.utc) < most_recent_opening:
        return now_aware  # current window opened after the last run
    next_opening = most_recent_opening + timedelta(days=1)
    jitter = (
        random.uniform(-JITTER_SECONDS, JITTER_SECONDS)
        if jitter_seconds is None
        else jitter_seconds
    )
    return (next_opening + timedelta(seconds=jitter)).astimezone(timezone.utc)


def _get_or_create_batch(
    db: Session, customer_id: int, batch_date: date, tz_name: str, size: int
) -> tuple[Batch, bool]:
    """Idempotent get-or-create on the uq_batches_customer_date constraint.

    Returns (batch, created): created=True when this call inserted the row.
    """
    batch = (
        db.query(Batch)
        .filter(Batch.customer_id == customer_id, Batch.batch_date == batch_date)
        .one_or_none()
    )
    if batch is not None:
        return batch, False
    try:
        batch = Batch(
            customer_id=customer_id,
            batch_date=batch_date,
            timezone=tz_name,
            size=size,
            status=BATCH_OPEN,
        )
        db.add(batch)
        db.flush()  # force the unique check inside the try block
        return batch, True
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(Batch)
            .filter(Batch.customer_id == customer_id, Batch.batch_date == batch_date)
            .one()
        )
        return existing, False


def run_scheduler_for_customer(
    db: Session,
    customer: Customer,
    batch_size: int | None = None,
    now: datetime | None = None,
) -> Batch | None:
    """Run one scheduling pass for a customer, immediately.

    This is the un-gated primitive: it always executes a pass (ensuring the
    current window's batch exists and sweeping eligible jobs). The worker
    path (run_scheduler_all) decides *whether* a pass is due via
    compute_next_run().

    Returns the (existing or newly created) Batch for the customer's current
    window date, or None when the customer's subscription is not billable
    (anything other than active/expiring_soon).
    """
    now = now or utcnow()

    subscription = get_subscription(db, customer)
    if subscription is None:
        logger.info(
            "scheduler.skip_no_subscription",
            customer_id=customer.id,
        )
        return None
    eff = effective_status(customer.status, subscription.expires_at, now)
    if eff not in (SUBSCRIPTION_ACTIVE, SUBSCRIPTION_EXPIRING_SOON):
        logger.info(
            "scheduler.skip_not_billable",
            customer_id=customer.id,
            effective_status=eff,
        )
        return None

    tz_name = customer.timezone or settings.DEFAULT_TIMEZONE
    start_time = customer.daily_start_time or DEFAULT_START_TIME
    # v2.0: the batch belongs to the current WINDOW (cutover at the
    # customer's start time), not necessarily the local calendar date.
    batch_date = window_date_for(tz_name, start_time, now)

    last_run = customer.last_scheduler_run_at
    if last_run is None or (now - last_run) > CATCHUP_AFTER:
        logger.info(
            "scheduler.catchup",
            customer_id=customer.id,
            last_run_at=last_run.isoformat() if last_run else None,
            window_date=batch_date.isoformat(),
        )

    batch, created = _get_or_create_batch(
        db,
        customer.id,
        batch_date,
        tz_name,
        batch_size or settings.BATCH_DEFAULT_SIZE,
    )
    if created:
        log_event(
            db,
            actor_id=None,
            action=BATCH_CREATED,
            entity_type="batch",
            entity_id=str(batch.id),
            meta={
                "customer_id": customer.id,
                "batch_date": batch_date.isoformat(),
                "timezone": tz_name,
                "daily_start_time": start_time,
            },
        )

    # Eligibility: only QUEUED jobs with no batch yet, from ACTIVE sources.
    # batch.size caps the TOTAL jobs in the batch (not per-run), and jobs
    # already past QUEUED keep their originating batch (window-cutover rule).
    already_assigned = (
        db.query(DownloadJob)
        .filter(DownloadJob.batch_id == batch.id)
        .count()
    )
    remaining = batch.size - already_assigned
    eligible: list[DownloadJob] = []
    if remaining > 0:
        eligible = (
            db.query(DownloadJob)
            .join(MediaItem, DownloadJob.media_item_id == MediaItem.id)
            .join(Source, MediaItem.source_id == Source.id)
            .filter(
                DownloadJob.customer_id == customer.id,
                DownloadJob.status == _ELIGIBLE_JOB_STATUS,
                DownloadJob.batch_id.is_(None),
                Source.status == SOURCE_ACTIVE,
            )
            .order_by(MediaItem.discovered_at.asc(), DownloadJob.id.asc())
            .limit(remaining)
            .all()
        )

    assigned = 0
    for job in eligible:
        try:
            validate_transition(job.status, BATCHED)
        except InvalidTransitionError:
            logger.warning(
                "scheduler.invalid_transition",
                job_id=job.id,
                from_status=job.status,
            )
            continue
        job.batch_id = batch.id
        job.status = BATCHED
        job.updated_at = now
        log_event(
            db,
            actor_id=None,
            action=JOB_STATUS_CHANGED,
            entity_type="download_job",
            entity_id=str(job.id),
            meta={"from": _ELIGIBLE_JOB_STATUS, "to": BATCHED, "batch_id": batch.id},
        )
        assigned += 1

    if assigned:
        batch.status = BATCH_ACTIVE

    customer.last_scheduler_run_at = now
    db.commit()
    db.refresh(batch)
    logger.info(
        "scheduler.batch_assigned",
        customer_id=customer.id,
        batch_id=batch.id,
        assigned=assigned,
    )
    return batch


def run_scheduler_all(
    db: Session, batch_size: int | None = None, now: datetime | None = None
) -> list[Batch]:
    """Run the scheduler for every customer that has a subscription row.

    v2.0: a customer is only given a pass when compute_next_run() says the
    pass is due (window opening reached, or missed-window catch-up). This —
    plus per-customer jitter — keeps the worker from stampeding every tick.
    """
    now = now or utcnow()
    now_aware = now.replace(tzinfo=timezone.utc)
    customer_ids = [
        row[0]
        for row in db.query(Customer.id)
        .join(Customer.subscription)
        .order_by(Customer.id.asc())
        .all()
    ]
    batches: list[Batch] = []
    for customer_id in customer_ids:
        customer = db.get(Customer, customer_id)
        if customer is None:
            continue
        next_run = compute_next_run(
            customer.timezone or settings.DEFAULT_TIMEZONE,
            customer.daily_start_time or DEFAULT_START_TIME,
            now,
            customer.last_scheduler_run_at,
        )
        if now_aware < next_run:
            logger.debug(
                "scheduler.skip_not_due",
                customer_id=customer.id,
                next_run=next_run.isoformat(),
            )
            continue
        batch = run_scheduler_for_customer(db, customer, batch_size=batch_size, now=now)
        if batch is not None:
            batches.append(batch)
    return batches

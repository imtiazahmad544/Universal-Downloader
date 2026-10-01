"""Batch scheduler: assign queued jobs to per-customer daily batches.

Midnight-cutover rule (structural, not conditional):
    Eligibility for batching is `status == 'QUEUED'` AND `batch_id IS NULL`.
    A job that has moved beyond QUEUED (e.g. BATCHED/READY/DOWNLOADING)
    always keeps its originating batch, even when the scheduler ticks past
    local midnight for the customer's timezone. The batch date is computed
    from the customer's local time at assignment time only; it is never
    recomputed or "rolled over" for jobs that already have a batch_id.
    Because `run_scheduler_for_customer()` is idempotent per
    (customer_id, batch_date) via the uq_batches_customer_date constraint,
    two scheduler ticks on the same local date always return the same batch.
"""

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
# the midnight-cutover rule is structural. Using the state-machine constants
# keeps the scheduler in lockstep with the central transition graph.
_ELIGIBLE_JOB_STATUS = QUEUED


def local_batch_info(tz_name: str, now_utc_naive: datetime) -> tuple[date, datetime]:
    """Convert a naive-UTC ``now`` into the customer's local batch date.

    Returns ``(local_batch_date, next_local_midnight_as_aware_utc)``.
    Unknown/invalid timezone names fall back to UTC. Pure helper (no DB).
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
    """Run one scheduling pass for a customer.

    Returns the (existing or newly created) Batch for the customer's current
    local date, or None when the customer's subscription is not billable
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
    batch_date, next_midnight_utc = local_batch_info(tz_name, now)
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
                "next_midnight_utc": next_midnight_utc.isoformat(),
            },
        )

    # Eligibility: only QUEUED jobs with no batch yet, from ACTIVE sources.
    # batch.size caps the TOTAL jobs in the batch (not per-run), and jobs
    # already past QUEUED keep their originating batch (midnight-cutover rule).
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
    """Run the scheduler for every customer that has a subscription row."""
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
        batch = run_scheduler_for_customer(db, customer, batch_size=batch_size, now=now)
        if batch is not None:
            batches.append(batch)
    return batches

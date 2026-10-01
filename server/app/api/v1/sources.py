"""Customer source management: list, create, update, remove, discover."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_customer_user, require_active_subscription
from app.core.canonicalize import canonicalize_source
from app.core.database import get_db
from app.models.models import (
    SOURCE_ACTIVE,
    SOURCE_PAUSED,
    SOURCE_REMOVED,
    Customer,
    DiscoveryRun,
    DownloadJob,
    MediaItem,
    Source,
    User,
)
from app.schemas.schemas import (
    DiscoveryRunRead,
    SourceCreate,
    SourceRead,
    SourceUpdate,
)
from app.services.audit import (
    DISCOVERY_REQUESTED,
    JOB_STATUS_CHANGED,
    SOURCE_CREATED,
    SOURCE_UPDATED,
    log_event,
)
from app.services.audit import SOURCE_REMOVED as AUDIT_SOURCE_REMOVED
from app.services.state_machine import (
    BATCHED,
    CANCELLED,
    QUEUED,
    READY,
    shortest_path,
    validate_transition,
)

router = APIRouter()

# Job states eligible for cancellation when their source is removed.
CANCELLABLE_STATES = (QUEUED, BATCHED, READY)


def _get_owned_source(db: Session, customer: Customer, source_id: int) -> Source | None:
    return (
        db.query(Source)
        .filter(Source.id == source_id, Source.customer_id == customer.id)
        .one_or_none()
    )


@router.get("/", response_model=list[SourceRead])
def list_sources(
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> list[Source]:
    _user, customer = dep
    return (
        db.query(Source)
        .filter(Source.customer_id == customer.id, Source.status != SOURCE_REMOVED)
        .order_by(Source.created_at)
        .all()
    )


@router.post("/", response_model=SourceRead, status_code=201)
def create_source(
    body: SourceCreate,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> Source:
    user, customer = dep
    try:
        canonical_id = canonicalize_source(body.platform, body.input_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = (
        db.query(Source)
        .filter(
            Source.customer_id == customer.id, Source.canonical_id == canonical_id
        )
        .one_or_none()
    )
    if existing is not None and existing.status != SOURCE_REMOVED:
        raise HTTPException(status_code=409, detail="source already exists")
    if existing is not None:
        # Revive a previously removed source instead of violating the
        # (customer_id, canonical_id) unique constraint.
        existing.status = SOURCE_ACTIVE
        existing.input_value = body.input_value
        existing.platform = body.platform
        source = existing
    else:
        source = Source(
            customer_id=customer.id,
            platform=body.platform,
            input_value=body.input_value,
            canonical_id=canonical_id,
            status=SOURCE_ACTIVE,
        )
        db.add(source)
    db.commit()
    db.refresh(source)
    log_event(
        db,
        actor_id=user.id,
        action=SOURCE_CREATED,
        entity_type="source",
        entity_id=source.id,
        meta={"platform": source.platform, "canonical_id": source.canonical_id},
    )
    return source


@router.patch("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: int,
    body: SourceUpdate,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> Source:
    user, customer = dep
    source = _get_owned_source(db, customer, source_id)
    if source is None or source.status == SOURCE_REMOVED:
        raise HTTPException(status_code=404, detail="source not found")
    if body.status is not None:
        if body.status not in (SOURCE_ACTIVE, SOURCE_PAUSED):
            raise HTTPException(
                status_code=400,
                detail=f"invalid source status: {body.status!r} "
                "(expected 'active' or 'paused')",
            )
        old = source.status
        source.status = body.status
        db.commit()
        db.refresh(source)
        log_event(
            db,
            actor_id=user.id,
            action=SOURCE_UPDATED,
            entity_type="source",
            entity_id=source.id,
            meta={"old_status": old, "new_status": source.status},
        )
    return source


@router.delete("/{source_id}", response_model=SourceRead)
def remove_source(
    source_id: int,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> Source:
    """Soft-remove a source and cancel its in-flight jobs.

    Idempotent: a second DELETE returns the unchanged row. Cancellation walks
    each job's shortest legal path to CANCELLED, validating every step
    (e.g. BATCHED -> PAUSED -> CANCELLED). Only QUEUED / BATCHED / READY
    jobs are touched; terminal and in-progress jobs, DownloadFile rows,
    MediaItems, and DiscoveryRuns are left alone.
    """
    user, customer = dep
    source = _get_owned_source(db, customer, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    if source.status == SOURCE_REMOVED:
        return source

    media_ids = [
        row[0]
        for row in db.query(MediaItem.id)
        .filter(MediaItem.source_id == source.id)
        .all()
    ]
    jobs: list[DownloadJob] = (
        db.query(DownloadJob)
        .filter(
            DownloadJob.customer_id == customer.id,
            DownloadJob.media_item_id.in_(media_ids),
            DownloadJob.status.in_(CANCELLABLE_STATES),
        )
        .all()
        if media_ids
        else []
    )

    cancelled_by_state: dict[str, int] = {}
    for job in jobs:
        from_status = job.status
        path = shortest_path(from_status, CANCELLED)
        if path is None:
            continue  # unreachable; leave the job untouched
        for step in path[1:]:
            validate_transition(job.status, step)  # validated at EVERY step
            job.status = step
        db.flush()
        cancelled_by_state[from_status] = cancelled_by_state.get(from_status, 0) + 1
        log_event(
            db,
            actor_id=user.id,
            action=JOB_STATUS_CHANGED,
            entity_type="job",
            entity_id=job.id,
            meta={"from": from_status, "to": CANCELLED, "reason": "source_removed"},
        )

    source.status = SOURCE_REMOVED
    db.commit()
    db.refresh(source)
    log_event(
        db,
        actor_id=user.id,
        action=AUDIT_SOURCE_REMOVED,
        entity_type="source",
        entity_id=source.id,
        meta={"cancelled_by_state": cancelled_by_state},
    )
    return source


@router.post("/{source_id}/discover", response_model=DiscoveryRunRead, status_code=202)
def request_discovery(
    source_id: int,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> DiscoveryRun:
    user, customer = dep
    source = _get_owned_source(db, customer, source_id)
    if source is None or source.status == SOURCE_REMOVED:
        raise HTTPException(status_code=404, detail="source not found")
    run = DiscoveryRun(source_id=source.id, provider="auto", status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)
    log_event(
        db,
        actor_id=user.id,
        action=DISCOVERY_REQUESTED,
        entity_type="discovery_run",
        entity_id=run.id,
        meta={"source_id": source.id, "provider": "auto"},
    )
    return run

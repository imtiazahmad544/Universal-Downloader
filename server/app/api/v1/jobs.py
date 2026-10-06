"""Customer job management: list, retry, pause, resume, status telemetry."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_customer_user, require_active_subscription
from app.core.database import get_db, utcnow
from app.models.models import (
    Customer,
    DownloadFile,
    DownloadJob,
    MediaItem,
    Source,
    User,
)
from app.schemas.schemas import JobRead, JobStatusUpdate
from app.services.audit import (
    JOB_PAUSED,
    JOB_RESUMED,
    JOB_RETRIED,
    JOB_STATUS_CHANGED,
    log_event,
)
from app.services.cookies import resolve_cookies_for_source
from app.services.state_machine import (
    CANCELLED,
    COMPLETED,
    DOWNLOADING,
    FAILED,
    PAUSED,
    QUEUED,
    READY,
    RETRY_WAIT,
    validate_transition,
)

router = APIRouter()


def _get_owned_job(db: Session, customer: Customer, job_id: int) -> DownloadJob | None:
    return (
        db.query(DownloadJob)
        .filter(DownloadJob.id == job_id, DownloadJob.customer_id == customer.id)
        .one_or_none()
    )


@router.get("/", response_model=list[JobRead])
def list_jobs(
    status: str | None = Query(default=None),
    batch_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> list[DownloadJob]:
    _user, customer = dep
    query = db.query(DownloadJob).filter(DownloadJob.customer_id == customer.id)
    if status is not None:
        query = query.filter(DownloadJob.status == status)
    if batch_id is not None:
        query = query.filter(DownloadJob.batch_id == batch_id)
    return query.order_by(DownloadJob.id.desc()).limit(limit).offset(offset).all()


@router.post("/{job_id}/retry", response_model=JobRead)
def retry_job(
    job_id: int,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> DownloadJob:
    user, customer = dep
    job = _get_owned_job(db, customer, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != FAILED:
        raise HTTPException(
            status_code=409, detail=f"only failed jobs can be retried (status: {job.status!r})"
        )
    validate_transition(job.status, READY)  # FAILED -> READY (v1.1)
    job.status = READY
    db.commit()
    db.refresh(job)
    log_event(
        db,
        actor_id=user.id,
        action=JOB_RETRIED,
        entity_type="job",
        entity_id=job.id,
        meta={"from": FAILED, "to": READY},
    )
    return job


@router.post("/{job_id}/pause", response_model=JobRead)
def pause_job(
    job_id: int,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> DownloadJob:
    """Pause a job. Allowed for expired/suspended customers (no subscription
    gate) since pausing is a safe, read-like control operation."""
    user, customer = dep
    job = _get_owned_job(db, customer, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from_status = job.status
    validate_transition(from_status, PAUSED)  # 409 via handler when invalid
    job.status = PAUSED
    db.commit()
    db.refresh(job)
    log_event(
        db,
        actor_id=user.id,
        action=JOB_PAUSED,
        entity_type="job",
        entity_id=job.id,
        meta={"from": from_status, "to": PAUSED},
    )
    return job


@router.post("/{job_id}/resume", response_model=JobRead)
def resume_job(
    job_id: int,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> DownloadJob:
    """Resume a paused job — or clear a captcha park (v2.0).

    Captcha flow: when extraction/download hits a captcha/bot-check wall the
    job is parked with captcha_required=True in a retryable state
    (RETRY_WAIT) and last_error_code='E_CAPTCHA'. After uploading cookies,
    this endpoint clears the flag and re-arms the job to READY so the next
    attempt resolves cookies (source override wins, else customer global)
    and retries WITH them. last_error_code is kept as history.
    """
    user, customer = dep
    job = _get_owned_job(db, customer, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    if job.captcha_required:
        if job.status not in (RETRY_WAIT, READY, FAILED):
            raise HTTPException(
                status_code=409,
                detail=f"captcha-flagged jobs can only resume from "
                f"{RETRY_WAIT!r}, {READY!r} or {FAILED!r} "
                f"(status: {job.status!r})",
            )
        # Best-effort cookies check: warn (audit) when nothing would be sent
        # with the retry, but don't block — the wall may have been transient.
        cookies_available = _job_cookies_available(db, job)
        from_status = job.status
        job.captcha_required = False
        if job.status != READY:
            validate_transition(job.status, READY)  # 409 via handler when invalid
            job.status = READY
        db.commit()
        db.refresh(job)
        log_event(
            db,
            actor_id=user.id,
            action=JOB_RESUMED,
            entity_type="job",
            entity_id=job.id,
            meta={
                "from": from_status,
                "restored_to": READY,
                "captcha_cleared": True,
                "cookies_available": cookies_available,
            },
        )
        return job

    if job.status != PAUSED:
        raise HTTPException(
            status_code=409, detail=f"only paused jobs can be resumed (status: {job.status!r})"
        )
    # PAUSED has no BATCHED transition, so a paused batched job resumes to
    # READY (keeping its batch_id); an unbatched job resumes to QUEUED.
    target = READY if job.batch_id is not None else QUEUED
    validate_transition(PAUSED, target)
    job.status = target
    db.commit()
    db.refresh(job)
    log_event(
        db,
        actor_id=user.id,
        action=JOB_RESUMED,
        entity_type="job",
        entity_id=job.id,
        meta={"from": PAUSED, "restored_to": target},
    )
    return job


def _job_cookies_available(db: Session, job: DownloadJob) -> bool:
    """True when resolve_cookies_for_source() would yield cookies for the
    job's source. Best-effort: any failure means 'not available'."""
    try:
        media = db.get(MediaItem, job.media_item_id)
        if media is None:
            return False
        source = db.get(Source, media.source_id)
        if source is None:
            return False
        return resolve_cookies_for_source(source, db) is not None
    except Exception:
        return False


@router.patch("/{job_id}/status", response_model=JobRead)
def report_job_status(
    job_id: int,
    body: JobStatusUpdate,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> DownloadJob:
    """Windows client telemetry: report a job status transition (plus
    optional progress and file info). Allowed for expired/suspended
    customers — reporting is read-like, not a new download."""
    user, customer = dep
    job = _get_owned_job(db, customer, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from_status = job.status
    if body.status != from_status:
        # Telemetry may re-report the current state (e.g. progress pings while
        # DOWNLOADING); only validate when the state actually changes.
        validate_transition(from_status, body.status)  # 409 via handler when invalid
        job.status = body.status
    if body.progress is not None:
        if not 0 <= body.progress <= 100:
            raise HTTPException(
                status_code=400, detail="progress must be between 0 and 100"
            )
        job.progress = body.progress
    if body.status == DOWNLOADING and from_status != DOWNLOADING:
        # Count an attempt only when (re-)entering DOWNLOADING, not on every
        # progress ping.
        job.attempts += 1
    if body.status == COMPLETED:
        if not body.file_path:
            raise HTTPException(
                status_code=400, detail="file_path is required when reporting COMPLETED"
            )
        existing = (
            db.query(DownloadFile)
            .filter(DownloadFile.job_id == job.id)
            .one_or_none()
        )
        if existing is None:
            db.add(
                DownloadFile(
                    job_id=job.id,
                    path=body.file_path,
                    size=body.file_size,
                    checksum=body.checksum,
                    completed_at=utcnow(),
                )
            )
        else:
            existing.path = body.file_path
            existing.size = body.file_size
            existing.checksum = body.checksum
            existing.completed_at = utcnow()
    db.commit()
    db.refresh(job)
    meta: dict = {"from": from_status, "to": body.status}
    if body.progress is not None:
        meta["progress"] = body.progress
    if body.error:
        meta["error"] = body.error
    log_event(
        db,
        actor_id=user.id,
        action=JOB_STATUS_CHANGED,
        entity_type="job",
        entity_id=job.id,
        meta=meta,
    )
    return job

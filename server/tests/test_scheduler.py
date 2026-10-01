"""Scheduler tests: idempotency, batch-size caps, eligibility rules,
midnight-cutover batch stability, and timezone handling."""

from datetime import date, datetime, timedelta, timezone

from app.core.database import utcnow
from app.models.models import Batch, DownloadJob
from app.services.scheduler import (
    local_batch_info,
    run_scheduler_all,
    run_scheduler_for_customer,
)
from tests.conftest import (
    make_batch,
    make_customer,
    make_job,
    make_media,
    make_source,
    make_subscription,
)

NOW = datetime(2026, 10, 1, 12, 0, 0)  # naive UTC


def _seed_queued_jobs(db, customer, source, n, url_tag="v"):
    jobs = []
    for i in range(n):
        media = make_media(
            db, customer, source, url=f"https://example.com/{url_tag}/{source.id}/{i}"
        )
        jobs.append(make_job(db, customer, media, status="queued"))
    return jobs


def _customer_with_stack(db, code, tz="UTC", sub_days=30):
    customer = make_customer(db, code=code, timezone=tz)
    make_subscription(db, customer, expires_at=NOW + timedelta(days=sub_days))
    source = make_source(db, customer, input_value="@handle")
    return customer, source


# ---------------------------------------------------------------------------
# Idempotency + size caps
# ---------------------------------------------------------------------------


def test_idempotent_same_local_day(db):
    customer, source = _customer_with_stack(db, "SCH1")
    _seed_queued_jobs(db, customer, source, 3)

    first = run_scheduler_for_customer(db, customer, batch_size=20, now=NOW)
    second = run_scheduler_for_customer(db, customer, batch_size=20, now=NOW)

    assert first is not None and second is not None
    assert first.id == second.id
    assert (
        db.query(Batch).filter(Batch.customer_id == customer.id).count() == 1
    )
    assert (
        db.query(DownloadJob)
        .filter(DownloadJob.customer_id == customer.id, DownloadJob.status == "batched")
        .count()
        == 3
    )


def test_batch_size_caps_total_assigned(db):
    customer, source = _customer_with_stack(db, "SCH2")
    _seed_queued_jobs(db, customer, source, 5)

    run_scheduler_for_customer(db, customer, batch_size=3, now=NOW)

    assert (
        db.query(DownloadJob)
        .filter(DownloadJob.customer_id == customer.id, DownloadJob.status == "batched")
        .count()
        == 3
    )
    assert (
        db.query(DownloadJob)
        .filter(DownloadJob.customer_id == customer.id, DownloadJob.status == "queued")
        .count()
        == 2
    )
    # Second run assigns nothing more: the size cap is TOTAL, not per-run.
    run_scheduler_for_customer(db, customer, batch_size=3, now=NOW)
    assert (
        db.query(DownloadJob)
        .filter(DownloadJob.customer_id == customer.id, DownloadJob.status == "batched")
        .count()
        == 3
    )


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_paused_source_jobs_untouched(db):
    customer, source = _customer_with_stack(db, "SCH3")
    _seed_queued_jobs(db, customer, source, 2)
    paused = make_source(
        db, customer, input_value="@pausedone", status="paused"
    )
    paused_jobs = _seed_queued_jobs(db, customer, paused, 2, url_tag="p")

    run_scheduler_for_customer(db, customer, batch_size=20, now=NOW)

    db.expire_all()
    assert all(j.status == "queued" for j in
               db.query(DownloadJob).filter(DownloadJob.id.in_([j.id for j in paused_jobs])).all())
    assert (
        db.query(DownloadJob)
        .filter(DownloadJob.customer_id == customer.id, DownloadJob.status == "batched")
        .count()
        == 2
    )


def test_removed_source_jobs_untouched(db):
    customer, source = _customer_with_stack(db, "SCH4")
    removed = make_source(
        db, customer, input_value="@gone", status="removed"
    )
    gone_jobs = _seed_queued_jobs(db, customer, removed, 2, url_tag="g")

    run_scheduler_for_customer(db, customer, batch_size=20, now=NOW)

    db.expire_all()
    assert all(
        j.status == "queued"
        for j in db.query(DownloadJob)
        .filter(DownloadJob.id.in_([j.id for j in gone_jobs]))
        .all()
    )
    assert (
        db.query(Batch).filter(Batch.customer_id == customer.id).count() == 1
    )


def test_expired_subscription_returns_none(db):
    customer = make_customer(db, code="SCH5")
    make_subscription(db, customer, expires_at=NOW - timedelta(days=1))
    source = make_source(db, customer, input_value="@handle")
    _seed_queued_jobs(db, customer, source, 2)

    assert run_scheduler_for_customer(db, customer, batch_size=20, now=NOW) is None
    assert db.query(Batch).filter(Batch.customer_id == customer.id).count() == 0


def test_suspended_customer_returns_none(db):
    customer = make_customer(db, code="SCH6", status="suspended")
    make_subscription(db, customer, expires_at=NOW + timedelta(days=30))
    source = make_source(db, customer, input_value="@handle")
    _seed_queued_jobs(db, customer, source, 2)

    # effective_status override (suspended beats dates) -> not billable.
    assert run_scheduler_for_customer(db, customer, batch_size=20, now=NOW) is None


def test_run_scheduler_all_skips_customer_without_subscription_row(db):
    ok_customer, ok_source = _customer_with_stack(db, "SCH7")
    _seed_queued_jobs(db, ok_customer, ok_source, 1)
    # Customer with NO subscription row at all.
    no_sub = make_customer(db, code="SCH8")
    no_sub_source = make_source(db, no_sub, input_value="@handle")
    _seed_queued_jobs(db, no_sub, no_sub_source, 1, url_tag="n")

    batches = run_scheduler_all(db, batch_size=20, now=NOW)

    assert [b.customer_id for b in batches] == [ok_customer.id]
    assert db.query(Batch).filter(Batch.customer_id == no_sub.id).count() == 0


# ---------------------------------------------------------------------------
# Midnight cutover: jobs past QUEUED keep their originating batch
# ---------------------------------------------------------------------------


def test_midnight_cutover_downloading_job_keeps_original_batch(db):
    customer, source = _customer_with_stack(db, "SCH9")
    jobs = _seed_queued_jobs(db, customer, source, 2)

    first = run_scheduler_for_customer(db, customer, batch_size=20, now=NOW)
    original_batch_id = first.id

    # Simulate the client starting job 0 the next day.
    job0 = db.get(DownloadJob, jobs[0].id)
    job0.status = "downloading"
    db.commit()

    next_day = NOW + timedelta(days=1)
    second = run_scheduler_for_customer(db, customer, batch_size=20, now=next_day)

    db.expire_all()
    job0 = db.get(DownloadJob, jobs[0].id)
    assert second.id != original_batch_id  # a new batch exists for the new local day
    assert job0.batch_id == original_batch_id  # in-progress job kept its batch
    assert job0.status == "downloading"


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------


def test_local_batch_info_asia_karachi():
    # 20:00 UTC on Oct 1 == 01:00 Oct 2 in Asia/Karachi (UTC+5, no DST).
    batch_date, next_midnight_utc = local_batch_info(
        "Asia/Karachi", datetime(2026, 10, 1, 20, 0, 0)
    )
    assert batch_date == date(2026, 10, 2)
    assert next_midnight_utc == datetime(2026, 10, 2, 19, 0, 0, tzinfo=timezone.utc)


def test_local_batch_info_unknown_zone_falls_back_to_utc():
    batch_date, next_midnight_utc = local_batch_info(
        "Mars/Olympus_Mons", datetime(2026, 10, 1, 20, 0, 0)
    )
    assert batch_date == date(2026, 10, 1)
    assert next_midnight_utc == datetime(2026, 10, 2, 0, 0, 0, tzinfo=timezone.utc)


def test_scheduler_uses_customer_local_date(db):
    customer, source = _customer_with_stack(db, "SCH10", tz="Asia/Karachi")
    _seed_queued_jobs(db, customer, source, 1)

    batch = run_scheduler_for_customer(
        db, customer, batch_size=20, now=datetime(2026, 10, 1, 20, 0, 0)
    )
    assert batch is not None
    assert batch.batch_date == date(2026, 10, 2)
    assert batch.timezone == "Asia/Karachi"

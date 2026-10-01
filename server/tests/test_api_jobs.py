"""Job API tests: pause/resume/retry/status telemetry, source-removal
cascade, and admin renew idempotency."""

from datetime import date

from app.core.database import utcnow
from app.models.models import (
    DownloadFile,
    DownloadJob,
    MediaItem,
    Source,
    Subscription,
    SubscriptionRenewal,
)
from tests.conftest import (
    auth_headers,
    make_batch,
    make_job,
    make_media,
    make_source,
    make_stack,
    make_user,
)


def _job_stack(db, client, code, **kwargs):
    stack = make_stack(db, client, code=code, **kwargs)
    source = make_source(db, stack.customer, input_value=f"@{code.lower()}")
    return stack, source


def _seed_job(db, stack, source, status, batch_id=None, url="https://example.com/v/1"):
    media = make_media(db, stack.customer, source, url=url)
    return make_job(db, stack.customer, media, status=status, batch_id=batch_id)


# ---------------------------------------------------------------------------
# pause / resume / retry
# ---------------------------------------------------------------------------


def test_pause_and_resume_unbatched_restores_queued(db, client):
    stack, source = _job_stack(db, client, "JOB1")
    job = _seed_job(db, stack, source, "ready", url="https://example.com/j1/1")

    paused = client.post(f"/api/v1/jobs/{job.id}/pause", headers=stack.headers)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resumed.status_code == 200
    # Unbatched jobs resume to QUEUED.
    assert resumed.json()["status"] == "queued"


def test_pause_and_resume_batched_restores_ready(db, client):
    stack, source = _job_stack(db, client, "JOB2")
    batch = make_batch(db, stack.customer, batch_date=date(2026, 10, 1))
    job = _seed_job(db, stack, source, "ready", batch_id=batch.id,
                    url="https://example.com/j2/1")

    assert client.post(f"/api/v1/jobs/{job.id}/pause", headers=stack.headers).status_code == 200
    resumed = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "ready"
    assert body["batch_id"] == batch.id  # the batch is kept


def test_pause_invalid_transition_409(db, client):
    stack, source = _job_stack(db, client, "JOB3")
    job = _seed_job(db, stack, source, "completed", url="https://example.com/j3/1")

    resp = client.post(f"/api/v1/jobs/{job.id}/pause", headers=stack.headers)
    assert resp.status_code == 409


def test_resume_requires_paused_409(db, client):
    stack, source = _job_stack(db, client, "JOB4")
    job = _seed_job(db, stack, source, "ready", url="https://example.com/j4/1")

    resp = client.post(f"/api/v1/jobs/{job.id}/resume", headers=stack.headers)
    assert resp.status_code == 409


def test_retry_failed_job(db, client):
    stack, source = _job_stack(db, client, "JOB5")
    job = _seed_job(db, stack, source, "failed", url="https://example.com/j5/1")

    resp = client.post(f"/api/v1/jobs/{job.id}/retry", headers=stack.headers)
    assert resp.status_code == 200
    # v1.1: manual retry goes FAILED -> READY.
    assert resp.json()["status"] == "ready"


def test_retry_non_failed_job_409(db, client):
    stack, source = _job_stack(db, client, "JOB6")
    job = _seed_job(db, stack, source, "queued", url="https://example.com/j6/1")

    resp = client.post(f"/api/v1/jobs/{job.id}/retry", headers=stack.headers)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /jobs/{id}/status telemetry
# ---------------------------------------------------------------------------


def test_status_update_invalid_transition_409(db, client):
    stack, source = _job_stack(db, client, "JOB7")
    job = _seed_job(db, stack, source, "completed", url="https://example.com/j7/1")

    resp = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={"status": "downloading"},
        headers=stack.headers,
    )
    assert resp.status_code == 409


def test_status_update_progress_over_100_400(db, client):
    stack, source = _job_stack(db, client, "JOB8")
    job = _seed_job(db, stack, source, "ready", url="https://example.com/j8/1")

    resp = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={"status": "downloading", "progress": 150},
        headers=stack.headers,
    )
    assert resp.status_code == 400


def test_status_update_completed_without_file_path_400(db, client):
    stack, source = _job_stack(db, client, "JOB9")
    job = _seed_job(db, stack, source, "downloading", url="https://example.com/j9/1")

    resp = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={"status": "completed"},
        headers=stack.headers,
    )
    assert resp.status_code == 400


def test_status_update_increments_attempts_and_records_progress(db, client):
    stack, source = _job_stack(db, client, "JOB10")
    job = _seed_job(db, stack, source, "ready", url="https://example.com/j10/1")

    resp = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={"status": "downloading", "progress": 45.5},
        headers=stack.headers,
    )
    assert resp.status_code == 200
    db.expire_all()
    job = db.get(DownloadJob, job.id)
    assert job.attempts == 1
    assert job.progress == 45.5


def test_status_update_same_status_is_noop_not_409(db, client):
    """Progress pings re-report the current state; they must not 409 and must
    not inflate the attempt counter (regression: client progress telemetry)."""
    stack, source = _job_stack(db, client, "JOB10B")
    job = _seed_job(db, stack, source, "downloading", url="https://example.com/j10b/1")

    resp = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={"status": "downloading", "progress": 62.0},
        headers=stack.headers,
    )
    assert resp.status_code == 200
    assert resp.json()["progress"] == 62.0
    db.expire_all()
    job = db.get(DownloadJob, job.id)
    assert job.status == "downloading"
    assert job.progress == 62.0
    assert job.attempts == 0  # no new attempt on a progress ping


def test_job_read_progress_never_null(db, client):
    """JobRead.progress coerces NULL (pre-gap-fill rows) to 0.0 so the API
    never emits null for the Windows client's non-nullable Progress."""
    stack, source = _job_stack(db, client, "JOB10C")
    job = _seed_job(db, stack, source, "ready", url="https://example.com/j10c/1")
    job.progress = None
    db.commit()

    resp = client.get("/api/v1/jobs", headers=stack.headers)
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    assert rows[job.id]["progress"] == 0.0


def test_status_update_completed_upserts_download_file(db, client):
    stack, source = _job_stack(db, client, "JOB11")
    job = _seed_job(db, stack, source, "downloading", url="https://example.com/j11/1")

    resp = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={
            "status": "completed",
            "file_path": "/dl/a.mp4",
            "file_size": 123,
            "checksum": "abc",
        },
        headers=stack.headers,
    )
    assert resp.status_code == 200
    files = db.query(DownloadFile).filter(DownloadFile.job_id == job.id).all()
    assert len(files) == 1
    assert files[0].path == "/dl/a.mp4"
    assert files[0].checksum == "abc"
    first_id = files[0].id

    # Re-reporting COMPLETED (after moving the job back) updates the same row.
    db.expire_all()
    job = db.get(DownloadJob, job.id)
    job.status = "downloading"
    db.commit()
    resp2 = client.patch(
        f"/api/v1/jobs/{job.id}/status",
        json={
            "status": "completed",
            "file_path": "/dl/a.mp4",
            "file_size": 200,
            "checksum": "def",
        },
        headers=stack.headers,
    )
    assert resp2.status_code == 200
    files = db.query(DownloadFile).filter(DownloadFile.job_id == job.id).all()
    assert len(files) == 1
    assert files[0].id == first_id  # upserted, not duplicated
    assert files[0].checksum == "def"
    assert files[0].size == 200


# ---------------------------------------------------------------------------
# DELETE /sources/{id}: removal cascade
# ---------------------------------------------------------------------------


def _cascade_stack(db, client, code):
    stack, source = _job_stack(db, client, code)
    batch = make_batch(db, stack.customer, batch_date=date(2026, 10, 1))
    job_queued = _seed_job(db, stack, source, "queued", url=f"https://example.com/{code}/q")
    job_batched = _seed_job(db, stack, source, "batched", batch_id=batch.id,
                            url=f"https://example.com/{code}/b")
    job_ready = _seed_job(db, stack, source, "ready", batch_id=batch.id,
                          url=f"https://example.com/{code}/r")
    job_completed = _seed_job(db, stack, source, "completed", batch_id=batch.id,
                              url=f"https://example.com/{code}/c")
    job_downloading = _seed_job(db, stack, source, "downloading", batch_id=batch.id,
                                url=f"https://example.com/{code}/d")
    # A finished file for the completed job must survive the cascade.
    media_c = db.get(DownloadJob, job_completed.id).media_item_id
    db.add(
        DownloadFile(
            job_id=job_completed.id,
            path="/dl/done.mp4",
            size=10,
            checksum="x",
            completed_at=utcnow(),
        )
    )
    db.commit()
    return stack, source, {
        "queued": job_queued.id,
        "batched": job_batched.id,
        "ready": job_ready.id,
        "completed": job_completed.id,
        "downloading": job_downloading.id,
    }


def test_delete_source_cascade(db, client):
    stack, source, ids = _cascade_stack(db, client, "DEL1")

    resp = client.delete(f"/api/v1/sources/{source.id}", headers=stack.headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"

    db.expire_all()
    status_of = lambda jid: db.get(DownloadJob, jid).status
    # QUEUED -> CANCELLED directly; BATCHED/READY -> CANCELLED via PAUSED.
    assert status_of(ids["queued"]) == "cancelled"
    assert status_of(ids["batched"]) == "cancelled"
    assert status_of(ids["ready"]) == "cancelled"
    # COMPLETED and DOWNLOADING are untouched.
    assert status_of(ids["completed"]) == "completed"
    assert status_of(ids["downloading"]) == "downloading"
    # Media items are intact.
    assert (
        db.query(MediaItem).filter(MediaItem.source_id == source.id).count() == 5
    )
    # DownloadFile rows are intact.
    assert (
        db.query(DownloadFile)
        .filter(DownloadFile.job_id == ids["completed"])
        .count()
        == 1
    )
    # Source row is soft-removed, not deleted.
    assert db.get(Source, source.id).status == "removed"


def test_delete_source_idempotent(db, client):
    stack, source, ids = _cascade_stack(db, client, "DEL2")

    first = client.delete(f"/api/v1/sources/{source.id}", headers=stack.headers)
    second = client.delete(f"/api/v1/sources/{source.id}", headers=stack.headers)

    assert first.status_code == 200
    assert second.status_code == 200  # idempotent: unchanged row returned
    db.expire_all()
    assert db.get(DownloadJob, ids["queued"]).status == "cancelled"


def test_delete_source_cross_tenant_404(db, client):
    stack, source, _ids = _cascade_stack(db, client, "DEL3")
    other = make_stack(db, client, code="DEL4")

    resp = client.delete(f"/api/v1/sources/{source.id}", headers=other.headers)
    assert resp.status_code == 404
    db.expire_all()
    assert db.get(Source, source.id).status == "active"  # untouched


# ---------------------------------------------------------------------------
# Admin renew idempotency
# ---------------------------------------------------------------------------


def test_renew_idempotency_same_key_one_renewal(db, client):
    stack = make_stack(db, client, code="REN1")
    make_user(db, customer=None, username="boss1", role="super_admin")
    admin_headers = auth_headers(client, "boss1")
    headers = {**admin_headers, "Idempotency-Key": "renew-key-1"}

    first = client.post(
        f"/api/v1/admin/customers/{stack.customer.id}/renew",
        json={"reason": "annual"},
        headers=headers,
    )
    second = client.post(
        f"/api/v1/admin/customers/{stack.customer.id}/renew",
        json={"reason": "annual"},
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()  # identical replayed body
    assert (
        db.query(SubscriptionRenewal)
        .join(Subscription, SubscriptionRenewal.subscription_id == Subscription.id)
        .filter(Subscription.customer_id == stack.customer.id)
        .count()
        == 1
    )


def test_renew_different_keys_renew_twice(db, client):
    stack = make_stack(db, client, code="REN2")
    make_user(db, customer=None, username="boss2", role="super_admin")
    admin_headers = auth_headers(client, "boss2")

    for key in ("k-a", "k-b"):
        resp = client.post(
            f"/api/v1/admin/customers/{stack.customer.id}/renew",
            json={},
            headers={**admin_headers, "Idempotency-Key": key},
        )
        assert resp.status_code == 200

    assert (
        db.query(SubscriptionRenewal)
        .join(Subscription, SubscriptionRenewal.subscription_id == Subscription.id)
        .filter(Subscription.customer_id == stack.customer.id)
        .count()
        == 2
    )

"""Subscription API tests: status read and the expired-customer gate.

An expired customer is blocked from writes (403) but keeps reads and
client telemetry (200)."""

from datetime import timedelta

from app.core.database import utcnow
from tests.conftest import make_job, make_media, make_source, make_stack


def test_get_subscription_returns_derived_fields(db, client):
    stack = make_stack(db, client, code="SUB1", subscription_days=30)

    resp = client.get("/api/v1/subscription", headers=stack.headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_status"] == "active"
    assert 29 <= body["days_remaining"] <= 30
    assert body["status"] == "active"


def test_get_subscription_expired_customer(db, client):
    stack = make_stack(db, client, code="SUB2", subscription_days=-5)

    resp = client.get("/api/v1/subscription", headers=stack.headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_status"] == "expired"
    assert body["days_remaining"] < 0


def _expired_stack(db, client, code):
    stack = make_stack(db, client, code=code, subscription_days=-5)
    source = make_source(db, stack.customer, input_value=f"@{code.lower()}")
    media = make_media(db, stack.customer, source, url=f"https://example.com/{code}/1")
    failed_job = make_job(db, stack.customer, media, status="failed")
    media2 = make_media(db, stack.customer, source, url=f"https://example.com/{code}/2")
    ready_job = make_job(db, stack.customer, media2, status="ready")
    media3 = make_media(db, stack.customer, source, url=f"https://example.com/{code}/3")
    queued_job = make_job(db, stack.customer, media3, status="queued")
    return stack, source, failed_job, ready_job, queued_job


def test_expired_customer_blocked_from_creating_source(db, client):
    stack, _source, _failed, _ready, _queued = _expired_stack(db, client, "SUB3")

    resp = client.post(
        "/api/v1/sources",
        json={"platform": "youtube", "input_value": "@newhandle"},
        headers=stack.headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "subscription not active"


def test_expired_customer_blocked_from_discovery(db, client):
    stack, source, _failed, _ready, _queued = _expired_stack(db, client, "SUB4")

    resp = client.post(
        f"/api/v1/sources/{source.id}/discover", headers=stack.headers
    )
    assert resp.status_code == 403


def test_expired_customer_blocked_from_retry(db, client):
    stack, _source, failed_job, _ready, _queued = _expired_stack(db, client, "SUB5")

    resp = client.post(f"/api/v1/jobs/{failed_job.id}/retry", headers=stack.headers)
    assert resp.status_code == 403


def test_expired_customer_reads_still_work(db, client):
    stack, _source, _failed, _ready, _queued = _expired_stack(db, client, "SUB6")

    assert client.get("/api/v1/jobs", headers=stack.headers).status_code == 200
    assert client.get("/api/v1/sources/", headers=stack.headers).status_code == 200
    assert client.get("/api/v1/batches/", headers=stack.headers).status_code == 200


def test_expired_customer_telemetry_still_works(db, client):
    stack, _source, _failed, ready_job, queued_job = _expired_stack(db, client, "SUB7")

    # Status telemetry bypasses the subscription gate.
    telemetry = client.patch(
        f"/api/v1/jobs/{ready_job.id}/status",
        json={"status": "downloading", "progress": 10.0},
        headers=stack.headers,
    )
    assert telemetry.status_code == 200
    assert telemetry.json()["status"] == "downloading"
    # ...and so does pause (QUEUED -> PAUSED is a legal, gate-free transition).
    assert (
        client.post(f"/api/v1/jobs/{queued_job.id}/pause", headers=stack.headers).status_code
        == 200
    )


def test_expiring_soon_customer_can_still_write(db, client):
    stack = make_stack(db, client, code="SUB8", subscription_days=2)

    resp = client.post(
        "/api/v1/sources",
        json={"platform": "youtube", "input_value": "@okhandle"},
        headers=stack.headers,
    )
    assert resp.status_code == 201

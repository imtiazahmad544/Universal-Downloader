"""Auth API tests: login, refresh rotation, RBAC, tenant isolation,
unauthenticated access, and health endpoints."""

from app.core.security import decode_access_token
from tests.conftest import (
    PASSWORD,
    auth_headers,
    login_tokens,
    make_batch,
    make_customer,
    make_job,
    make_media,
    make_source,
    make_stack,
    make_user,
)


def test_login_success_by_username(client, db):
    stack = make_stack(db, client, code="AUTH1")

    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": "AUTH1", "password": PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"

    payload = decode_access_token(body["access_token"])
    assert int(payload["sub"]) == stack.user.id
    assert payload["role"] == "customer"


def test_login_success_by_customer_code(client, db):
    # The login user may have a username different from the customer code;
    # the customer_code lookup path must still find them.
    customer = make_customer(db, code="AUTH2")
    make_user(db, customer, username="auth2_login")

    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": "AUTH2", "password": PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_login_wrong_password_401(client, db):
    make_stack(db, client, code="AUTH3")

    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": "AUTH3", "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_unknown_identifier_401(client, db):
    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": "NOPE", "password": PASSWORD},
    )
    assert resp.status_code == 401


def test_login_disabled_user_401(client, db):
    customer = make_customer(db, code="AUTH4")
    make_user(db, customer, status="disabled")

    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": "AUTH4", "password": PASSWORD},
    )
    assert resp.status_code == 401


def test_refresh_rotation(client, db):
    make_stack(db, client, code="AUTH5")
    pair = login_tokens(client, "AUTH5")

    rotated = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert rotated.status_code == 200
    new_pair = rotated.json()
    assert new_pair["access_token"]
    assert new_pair["refresh_token"] != pair["refresh_token"]

    # The old refresh token is revoked: reuse is rejected.
    reuse = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert reuse.status_code == 401

    # And the new one works.
    again = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_pair["refresh_token"]}
    )
    assert again.status_code == 200


def test_refresh_invalid_token_401(client, db):
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "bogus-token-value"}
    )
    assert resp.status_code == 401


def test_customer_cannot_access_admin_403(client, db):
    stack = make_stack(db, client, code="AUTH6")

    assert (
        client.get("/api/v1/admin/customers", headers=stack.headers).status_code == 403
    )


def test_admin_can_access_admin(client, db):
    make_user(db, customer=None, username="admin1", role="super_admin")
    headers = auth_headers(client, "admin1")

    resp = client.get("/api/v1/admin/customers", headers=headers)
    assert resp.status_code == 200


def test_tenant_isolation(client, db):
    stack_a = make_stack(db, client, code="AUTH7")
    stack_b = make_stack(db, client, code="AUTH8")

    src_b = make_source(db, stack_b.customer, input_value="@bhandle")
    media_b = make_media(db, stack_b.customer, src_b, url="https://example.com/b/1")
    job_b = make_job(db, stack_b.customer, media_b, status="ready")
    batch_b = make_batch(db, stack_b.customer)

    # Customer A sees none of B's rows: 404 on direct access...
    assert (
        client.patch(
            f"/api/v1/sources/{src_b.id}",
            json={"status": "paused"},
            headers=stack_a.headers,
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/sources/{src_b.id}", headers=stack_a.headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/jobs/{job_b.id}/pause", headers=stack_a.headers
        ).status_code
        == 404
    )
    # ...and B's rows are absent from A's listings.
    assert client.get("/api/v1/sources/", headers=stack_a.headers).json() == []
    assert client.get("/api/v1/jobs/", headers=stack_a.headers).json() == []
    batches = client.get("/api/v1/batches/", headers=stack_a.headers).json()
    assert all(b["id"] != batch_b.id for b in batches)


def test_unauthenticated_401(client, db):
    make_stack(db, client, code="AUTH9")

    assert client.get("/api/v1/").status_code == 401
    assert client.get("/api/v1/subscription").status_code == 401
    assert client.get("/api/v1/jobs/").status_code == 401


def test_invalid_bearer_token_401(client, db):
    resp = client.get(
        "/api/v1/jobs/", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_health_and_ready_200(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"

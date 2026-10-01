"""Regression tests: a customer with NO subscription row must be treated as
not-billable everywhere — never silently provisioned a free month.

Covers the fix for the auto-provisioning hole in get_or_create_subscription:
- API write guard (deps.require_active_subscription) -> 403, no row created
- Scheduler per-customer run -> None, no row created, no batch
- GET /api/v1/subscription -> 404
- GET /api/v1/me -> subscription null
- Admin renew on a subscription-less customer provisions exactly ONE month
  (not two) plus a single renewal ledger row.
"""

from datetime import timedelta

from app.core.database import utcnow
from app.models import Batch, Subscription, SubscriptionRenewal
from app.services.scheduler import run_scheduler_for_customer
from app.services.subscriptions import add_months
from tests.conftest import auth_headers, make_customer, make_stack, make_user


def test_write_op_blocked_and_nothing_provisioned(db, client):
    stack = make_stack(db, client, code="NOSUB1", subscription_days=None)

    resp = client.post(
        "/api/v1/sources",
        json={"platform": "youtube", "input_value": "@handle"},
        headers=stack.headers,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "subscription not active"
    # The guard must NOT have created a subscription as a side effect.
    assert (
        db.query(Subscription)
        .filter(Subscription.customer_id == stack.customer.id)
        .count()
        == 0
    )


def test_scheduler_per_customer_skips_and_provisions_nothing(db):
    customer = make_customer(db, code="NOSUB2")

    result = run_scheduler_for_customer(db, customer, batch_size=5)

    assert result is None
    assert (
        db.query(Subscription).filter(Subscription.customer_id == customer.id).count()
        == 0
    )
    assert db.query(Batch).filter(Batch.customer_id == customer.id).count() == 0


def test_get_subscription_404_without_row(db, client):
    stack = make_stack(db, client, code="NOSUB3", subscription_days=None)

    resp = client.get("/api/v1/subscription", headers=stack.headers)

    assert resp.status_code == 404


def test_me_returns_null_subscription_without_row(db, client):
    stack = make_stack(db, client, code="NOSUB4", subscription_days=None)

    resp = client.get("/api/v1/", headers=stack.headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["customer"]["customer_code"] == "NOSUB4"
    assert body["subscription"] is None


def test_admin_renew_provisions_exactly_one_month(db, client):
    stack = make_stack(db, client, code="NOSUB5", subscription_days=None)
    make_user(db, customer=None, username="bossns", role="super_admin")
    admin_headers = auth_headers(client, "bossns")

    before = utcnow()
    resp = client.post(
        f"/api/v1/admin/customers/{stack.customer.id}/renew",
        json={"reason": "first provisioning"},
        headers=admin_headers,
    )
    after = utcnow()

    assert resp.status_code == 200
    sub = (
        db.query(Subscription)
        .filter(Subscription.customer_id == stack.customer.id)
        .one()
    )
    delta = sub.expires_at - before
    # Exactly one calendar month — not two (the old double-provision bug).
    assert timedelta(days=27) < delta < timedelta(days=32)
    assert sub.expires_at <= add_months(after, 1)
    renewals = (
        db.query(SubscriptionRenewal)
        .filter(SubscriptionRenewal.subscription_id == sub.id)
        .all()
    )
    assert len(renewals) == 1
    assert renewals[0].reason == "first provisioning"

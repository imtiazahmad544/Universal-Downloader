"""Subscription lifecycle tests: status derivation, effective status,
days_remaining, calendar-month arithmetic, and transactional renewal."""

from datetime import datetime, timedelta

import pytest

from app.core.database import utcnow
from app.models.models import (
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_EXPIRED,
    SUBSCRIPTION_EXPIRING_SOON,
    SubscriptionRenewal,
)
from app.services.subscriptions import (
    WARNING_DAYS,
    add_months,
    days_remaining,
    derive_subscription_status,
    effective_status,
    renew_subscription,
)
from tests.conftest import make_customer, make_subscription


def _now():
    return utcnow()


# ---------------------------------------------------------------------------
# derive_subscription_status
# ---------------------------------------------------------------------------


def test_derive_active():
    now = _now()
    assert derive_subscription_status(now + timedelta(days=30), now) == SUBSCRIPTION_ACTIVE


def test_derive_expiring_soon():
    now = _now()
    assert derive_subscription_status(now + timedelta(days=2), now) == SUBSCRIPTION_EXPIRING_SOON


def test_derive_expiring_soon_boundary_exactly_warning_days():
    # Exactly now + 3 days (WARNING_DAYS) is still expiring_soon.
    now = _now()
    assert WARNING_DAYS == 3
    assert (
        derive_subscription_status(now + timedelta(days=WARNING_DAYS), now)
        == SUBSCRIPTION_EXPIRING_SOON
    )


def test_derive_expired():
    now = _now()
    assert derive_subscription_status(now - timedelta(days=1), now) == SUBSCRIPTION_EXPIRED


def test_derive_expired_boundary_expires_at_equals_now():
    now = _now()
    assert derive_subscription_status(now, now) == SUBSCRIPTION_EXPIRED


# ---------------------------------------------------------------------------
# effective_status
# ---------------------------------------------------------------------------


def test_effective_status_suspended_overrides_dates():
    now = _now()
    far_future = now + timedelta(days=365)
    assert effective_status("suspended", far_future, now) == "suspended"


def test_effective_status_disabled_overrides_dates():
    now = _now()
    far_future = now + timedelta(days=365)
    assert effective_status("disabled", far_future, now) == "disabled"


def test_effective_status_active_customer_follows_dates():
    now = _now()
    assert (
        effective_status("active", now + timedelta(days=30), now) == SUBSCRIPTION_ACTIVE
    )
    assert (
        effective_status("active", now - timedelta(days=1), now) == SUBSCRIPTION_EXPIRED
    )


# ---------------------------------------------------------------------------
# days_remaining
# ---------------------------------------------------------------------------


def test_days_remaining_floor():
    now = _now()
    assert days_remaining(now + timedelta(days=10, hours=5), now) == 10


def test_days_remaining_negative_when_expired():
    now = _now()
    assert days_remaining(now - timedelta(days=2), now) == -2
    # .days floors toward negative infinity on timedeltas.
    assert days_remaining(now - timedelta(days=2, hours=1), now) == -3


def test_days_remaining_zero_on_expiry_day():
    now = _now()
    assert days_remaining(now + timedelta(hours=12), now) == 0


# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------


def test_add_months_plain():
    assert add_months(datetime(2026, 1, 15), 1) == datetime(2026, 2, 15)


def test_add_months_year_rollover():
    assert add_months(datetime(2026, 12, 15), 1) == datetime(2027, 1, 15)


def test_add_months_jan31_to_feb28():
    assert add_months(datetime(2025, 1, 31), 1) == datetime(2025, 2, 28)


def test_add_months_jan31_leap_year_to_feb29():
    assert add_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)


def test_add_months_multiple_months():
    assert add_months(datetime(2026, 10, 1), 3) == datetime(2027, 1, 1)


# ---------------------------------------------------------------------------
# renew_subscription
# ---------------------------------------------------------------------------


def test_renew_from_active_extends_from_existing_expiry(db):
    customer = make_customer(db)
    old_expiry = utcnow() + timedelta(days=10)
    subscription = make_subscription(db, customer, expires_at=old_expiry)

    renewal = renew_subscription(db, subscription, operator_id=None, reason="test")

    # Base is max(existing expiry, now): the existing expiry wins here.
    assert subscription.expires_at == add_months(old_expiry, 1)
    assert renewal.old_expiry == old_expiry
    assert renewal.new_expiry == add_months(old_expiry, 1)
    assert renewal.reason == "test"
    # Exactly one ledger row, and the subscription status was recomputed.
    assert db.query(SubscriptionRenewal).count() == 1
    assert subscription.status == SUBSCRIPTION_ACTIVE


def test_renew_from_expired_bases_on_now(db):
    customer = make_customer(db)
    old_expiry = utcnow() - timedelta(days=5)
    subscription = make_subscription(db, customer, expires_at=old_expiry)

    renewal = renew_subscription(db, subscription, reason="late renewal")

    # Base is max(existing expiry, now): now wins; new expiry is ~1 month out.
    expected = add_months(utcnow(), 1)
    skew = abs((subscription.expires_at - expected).total_seconds())
    assert skew < 60
    assert renewal.old_expiry == old_expiry
    assert renewal.new_expiry == subscription.expires_at
    assert db.query(SubscriptionRenewal).count() == 1
    assert subscription.status == SUBSCRIPTION_ACTIVE


def test_renew_records_operator_and_reason(db):
    from tests.conftest import make_user

    customer = make_customer(db)
    admin = make_user(db, customer=None, username="op-admin", role="super_admin")
    subscription = make_subscription(db, customer, expires_at=utcnow() + timedelta(days=1))

    renewal = renew_subscription(db, subscription, operator_id=admin.id, reason="vip")

    db.refresh(renewal)
    assert renewal.operator_id == admin.id
    assert renewal.reason == "vip"


def test_renew_twice_appends_two_ledger_rows(db):
    customer = make_customer(db)
    subscription = make_subscription(db, customer, expires_at=utcnow() + timedelta(days=10))

    first = renew_subscription(db, subscription)
    second = renew_subscription(db, subscription)

    assert db.query(SubscriptionRenewal).count() == 2
    assert second.old_expiry == first.new_expiry
    assert second.new_expiry == add_months(first.new_expiry, 1)

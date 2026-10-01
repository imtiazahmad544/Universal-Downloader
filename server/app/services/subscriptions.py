"""Subscription lifecycle: status derivation and renewal.

Datetime convention: naive UTC everywhere; "now" is always utcnow().
"""

import calendar
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models.models import (
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_EXPIRED,
    SUBSCRIPTION_EXPIRING_SOON,
    Customer,
    Subscription,
    SubscriptionRenewal,
)

WARNING_DAYS = 3


def derive_subscription_status(expires_at: datetime, now: datetime) -> str:
    """Derive the subscription status purely from its expiry timestamp."""
    if expires_at <= now:
        return SUBSCRIPTION_EXPIRED
    if expires_at <= now + timedelta(days=WARNING_DAYS):
        return SUBSCRIPTION_EXPIRING_SOON
    return SUBSCRIPTION_ACTIVE


def effective_status(customer_status: str, expires_at: datetime, now: datetime) -> str:
    """Customer-level suspension/disability overrides the subscription status."""
    if customer_status in ("suspended", "disabled"):
        return customer_status
    return derive_subscription_status(expires_at, now)


def days_remaining(expires_at: datetime, now: datetime) -> int:
    """Whole days left until expiry (floor; negative when expired)."""
    return (expires_at - now).days


def add_months(dt: datetime, months: int = 1) -> datetime:
    """Calendar-month arithmetic, clamping month ends (Jan 31 -> Feb 28/29)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def refresh_subscription_status(db: Session, subscription: Subscription) -> str:
    """Recompute the status from expires_at, persist it, and return it."""
    status = derive_subscription_status(subscription.expires_at, utcnow())
    subscription.status = status
    db.commit()
    db.refresh(subscription)
    return status


def get_subscription(db: Session, customer: Customer) -> Subscription | None:
    """Pure lookup: return the customer's subscription row, or None.

    A missing row means "no subscription" and must be treated as
    not-billable (expired) by callers — never silently provisioned.
    """
    return (
        db.query(Subscription)
        .filter(Subscription.customer_id == customer.id)
        .one_or_none()
    )


def provision_subscription(
    db: Session,
    customer: Customer,
    operator_id: int | None = None,
    reason: str | None = None,
) -> tuple[Subscription, SubscriptionRenewal]:
    """First-time provisioning: create a 1-month subscription for a customer
    that has none, plus its initial renewal ledger row. Transactional.
    Only appropriate for explicit administrative action (e.g. admin renew)."""
    now = utcnow()
    subscription = Subscription(
        customer_id=customer.id,
        starts_at=now,
        expires_at=add_months(now, 1),
        status=SUBSCRIPTION_ACTIVE,
    )
    db.add(subscription)
    db.flush()
    renewal = SubscriptionRenewal(
        subscription_id=subscription.id,
        operator_id=operator_id,
        old_expiry=now,
        new_expiry=subscription.expires_at,
        renewed_at=now,
        reason=reason,
    )
    db.add(renewal)
    db.commit()
    db.refresh(subscription)
    db.refresh(renewal)
    return subscription, renewal


def get_or_create_subscription(db: Session, customer: Customer) -> Subscription:
    """Legacy wrapper: return the customer's subscription, creating a fresh
    1-month one when none exists.

    NOTE: prefer get_subscription() + explicit provision_subscription().
    Auto-creation must never happen on read paths (API guards, scheduler),
    where a missing subscription means "not active".
    """
    subscription = get_subscription(db, customer)
    if subscription is not None:
        return subscription
    subscription, _renewal = provision_subscription(db, customer)
    return subscription


def renew_subscription(
    db: Session,
    subscription: Subscription,
    operator_id: int | None = None,
    reason: str | None = None,
) -> SubscriptionRenewal:
    """Renew a subscription by one calendar month, atomically.

    The new expiry is computed from max(current expiry, now) so renewing an
    already-expired subscription starts the month from now rather than from
    the lapsed date. The subscription row is updated and an immutable
    SubscriptionRenewal ledger row is inserted in a single transaction.
    """
    now = utcnow()
    old_expiry = subscription.expires_at
    base = max(old_expiry, now)
    new_expiry = add_months(base, 1)

    subscription.expires_at = new_expiry
    subscription.status = derive_subscription_status(new_expiry, now)

    renewal = SubscriptionRenewal(
        subscription_id=subscription.id,
        operator_id=operator_id,
        old_expiry=old_expiry,
        new_expiry=new_expiry,
        renewed_at=now,
        reason=reason,
    )
    db.add(renewal)
    db.commit()  # single transaction: subscription update + renewal insert
    db.refresh(subscription)
    db.refresh(renewal)
    return renewal

"""Shared read-model helpers for the API layer."""

from datetime import datetime

from app.core.database import utcnow
from app.models.models import Customer, Subscription
from app.schemas.schemas import SubscriptionRead
from app.services.subscriptions import days_remaining, effective_status


def subscription_payload(
    subscription: Subscription, customer_status: str, now: datetime | None = None
) -> dict:
    """JSON-ready subscription summary with derived fields computed."""
    now = now or utcnow()
    return {
        "id": subscription.id,
        "customer_id": subscription.customer_id,
        "starts_at": subscription.starts_at,
        "expires_at": subscription.expires_at,
        "status": subscription.status,
        "effective_status": effective_status(
            customer_status, subscription.expires_at, now
        ),
        "days_remaining": days_remaining(subscription.expires_at, now),
    }


def subscription_read(
    subscription: Subscription, customer: Customer, now: datetime | None = None
) -> SubscriptionRead:
    """Build a SubscriptionRead with effective_status + days_remaining."""
    return SubscriptionRead(
        **subscription_payload(subscription, customer.status, now)
    )

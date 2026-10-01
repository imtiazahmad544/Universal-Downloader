"""Customer subscription status."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_customer_user
from app.api.helpers import subscription_read
from app.core.database import get_db
from app.models.models import Customer, User
from app.schemas.schemas import SubscriptionRead
from app.services.subscriptions import (
    get_subscription as fetch_subscription,
    refresh_subscription_status,
)

router = APIRouter()


@router.get("/", response_model=SubscriptionRead)
def get_subscription(
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> SubscriptionRead:
    _user, customer = dep
    subscription = fetch_subscription(db, customer)
    if subscription is None:
        raise HTTPException(status_code=404, detail="no subscription for customer")
    refresh_subscription_status(db, subscription)
    return subscription_read(subscription, customer)

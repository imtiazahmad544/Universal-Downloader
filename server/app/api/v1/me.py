"""GET /api/v1/ — the authenticated caller's profile, customer, subscription."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.helpers import subscription_read
from app.core.database import get_db
from app.models.models import ROLE_CUSTOMER, Customer, User
from app.schemas.schemas import CustomerRead, SubscriptionRead, UserRead
from app.services.subscriptions import (
    get_subscription,
    refresh_subscription_status,
)

router = APIRouter()


@router.get("/", response_model=dict)
def me(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    customer: Customer | None = None
    subscription: SubscriptionRead | None = None
    if user.role == ROLE_CUSTOMER and user.customer_id is not None:
        customer = (
            db.query(Customer).filter(Customer.id == user.customer_id).one_or_none()
        )
        if customer is not None:
            sub = get_subscription(db, customer)
            if sub is not None:
                refresh_subscription_status(db, sub)
                subscription = subscription_read(sub, customer)
    return {
        "user": UserRead.model_validate(user),
        "customer": CustomerRead.model_validate(customer) if customer else None,
        "subscription": subscription,
    }

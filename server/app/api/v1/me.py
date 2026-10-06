"""GET /api/v1/ — the authenticated caller's profile, customer, subscription."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_customer_user,
    require_active_subscription,
)
from app.api.helpers import subscription_read
from app.core.database import get_db
from app.models.models import ROLE_CUSTOMER, Customer, User
from app.schemas.schemas import (
    CookiesStatusRead,
    CustomerRead,
    CustomerSettingsUpdate,
    SubscriptionRead,
    UserRead,
)
from app.services.audit import (
    COOKIES_DELETED,
    COOKIES_UPDATED,
    CUSTOMER_SETTINGS_UPDATED,
    log_event,
)
from app.services.cookies import (
    MAX_COOKIES_BYTES,
    clear_customer_cookies,
    cookies_status,
    store_customer_cookies,
    validate_cookies_format,
)
from app.services.scheduler import validate_daily_start_time
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


@router.patch("/me/settings", response_model=CustomerRead)
def update_my_settings(
    body: CustomerSettingsUpdate,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> Customer:
    """Update the caller's customer settings (v2.0).

    Currently: daily_start_time ("HH:MM", 24h) — the local time the daily
    batch window opens in the customer's timezone.
    """
    user, customer = dep
    try:
        validate_daily_start_time(body.daily_start_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    old = customer.daily_start_time
    customer.daily_start_time = body.daily_start_time.strip()
    db.commit()
    db.refresh(customer)
    log_event(
        db,
        actor_id=user.id,
        action=CUSTOMER_SETTINGS_UPDATED,
        entity_type="customer",
        entity_id=str(customer.id),
        meta={
            "daily_start_time": {"old": old, "new": customer.daily_start_time}
        },
    )
    return customer


# ---------------------------------------------------------------------------
# Cookies (v2.0, optional): customer-global encrypted Netscape cookies.
# Raw values are NEVER returned; GETs report presence only.
# ---------------------------------------------------------------------------


@router.put("/me/cookies", response_model=CookiesStatusRead)
async def upload_my_cookies(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> dict:
    """Upload (multipart field `file`) and encrypt the customer's global
    cookies — the fallback for every source without its own cookies."""
    user, customer = dep
    raw = await file.read()
    if len(raw) > MAX_COOKIES_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"cookies file too large (max {MAX_COOKIES_BYTES} bytes)",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="cookies file must be UTF-8 text"
        ) from exc
    try:
        validate_cookies_format(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        store_customer_cookies(db, customer, text)
    except RuntimeError as exc:  # key misconfiguration (prod without key)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    db.commit()
    db.refresh(customer)
    log_event(
        db,
        actor_id=user.id,
        action=COOKIES_UPDATED,
        entity_type="customer",
        entity_id=str(customer.id),
        meta={"scope": "customer"},
    )
    return cookies_status(customer)


@router.get("/me/cookies", response_model=CookiesStatusRead)
def get_my_cookies_status(
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> dict:
    """Presence-only cookies status. Raw values are never returned."""
    _user, customer = dep
    return cookies_status(customer)


@router.delete("/me/cookies", response_model=CookiesStatusRead)
def delete_my_cookies(
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(require_active_subscription),
) -> dict:
    user, customer = dep
    clear_customer_cookies(db, customer)
    db.commit()
    db.refresh(customer)
    log_event(
        db,
        actor_id=user.id,
        action=COOKIES_DELETED,
        entity_type="customer",
        entity_id=str(customer.id),
        meta={"scope": "customer"},
    )
    return cookies_status(customer)

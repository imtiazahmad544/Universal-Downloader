"""Admin endpoints: customers, renewals, audit log.

All routes require role super_admin or admin. Customer rows are never
deleted; subscriptions renew through the append-only ledger.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.api.helpers import subscription_payload
from app.core.database import utcnow
from app.core.security import hash_password
from app.models.models import (
    CUSTOMER_ACTIVE,
    CUSTOMER_DISABLED,
    CUSTOMER_SUSPENDED,
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_SUPER_ADMIN,
    AuditLog,
    Customer,
    IdempotencyKey,
    Subscription,
    User,
)
from app.schemas.schemas import (
    AuditLogRead,
    CustomerRead,
    CustomerUpdate,
    RenewalRead,
    RenewalRequest,
)
from app.services.audit import (
    CUSTOMER_CREATED,
    CUSTOMER_STATUS_CHANGED,
    SUBSCRIPTION_RENEWED,
    log_event,
)
from app.services.subscriptions import (
    add_months,
    derive_subscription_status,
    get_subscription,
    provision_subscription,
    renew_subscription,
)

router = APIRouter()

_admin_only = require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN)


class CustomerCreateAdmin(BaseModel):
    customer_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    months: int = Field(default=1, ge=1)


VALID_CUSTOMER_STATUSES = (CUSTOMER_ACTIVE, CUSTOMER_SUSPENDED, CUSTOMER_DISABLED)


def _customer_summary(
    customer: Customer, subscription: Subscription | None, now=None
) -> dict:
    payload = CustomerRead.model_validate(customer).model_dump(mode="json")
    payload["timezone"] = customer.timezone
    payload["subscription"] = (
        subscription_payload(subscription, customer.status, now)
        if subscription is not None
        else None
    )
    return payload


@router.get("/customers")
def list_customers(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    expiring: bool | None = Query(default=None),
    expired: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(_admin_only),
) -> list[dict]:
    now = utcnow()
    query = db.query(Customer)
    if status is not None:
        query = query.filter(Customer.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Customer.customer_code.ilike(like)) | (Customer.name.ilike(like))
        )
    customers = query.order_by(Customer.id).limit(limit).offset(offset).all()

    results: list[dict] = []
    for customer in customers:
        subscription = (
            db.query(Subscription)
            .filter(Subscription.customer_id == customer.id)
            .one_or_none()
        )
        if expiring is True or expired is True:
            if subscription is None:
                continue
            derived = derive_subscription_status(subscription.expires_at, now)
            if expiring is True and derived != "expiring_soon":
                continue
            if expired is True and derived != "expired":
                continue
        results.append(_customer_summary(customer, subscription, now))
    return results


@router.post("/customers", response_model=CustomerRead, status_code=201)
def create_customer(
    body: CustomerCreateAdmin,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> Customer:
    if (
        db.query(Customer)
        .filter(Customer.customer_code == body.customer_code)
        .one_or_none()
        is not None
    ):
        raise HTTPException(status_code=409, detail="customer_code already taken")
    if (
        db.query(User).filter(User.username == body.customer_code).one_or_none()
        is not None
    ):
        raise HTTPException(status_code=409, detail="username already taken")
    now = utcnow()
    customer = Customer(
        customer_code=body.customer_code,
        name=body.name,
        contact_email=body.contact_email,
        contact_phone=body.contact_phone,
        status=CUSTOMER_ACTIVE,
    )
    db.add(customer)
    db.flush()  # assign customer.id for the dependent rows
    user = User(
        customer_id=customer.id,
        role=ROLE_CUSTOMER,
        username=body.customer_code,
        password_hash=hash_password(body.password),
        status="active",
    )
    subscription = Subscription(
        customer_id=customer.id,
        starts_at=now,
        expires_at=add_months(now, body.months),
        status="active",
    )
    db.add(user)
    db.add(subscription)
    db.commit()  # one transaction: customer + login user + subscription
    db.refresh(customer)
    log_event(
        db,
        actor_id=admin.id,
        action=CUSTOMER_CREATED,
        entity_type="customer",
        entity_id=customer.id,
        meta={"customer_code": customer.customer_code, "months": body.months},
    )
    return customer


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> Customer:
    customer = db.query(Customer).filter(Customer.id == customer_id).one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    if body.status is not None and body.status not in VALID_CUSTOMER_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid customer status: {body.status!r}",
        )
    old_status = customer.status
    if body.name is not None:
        customer.name = body.name
    if body.contact_email is not None:
        customer.contact_email = body.contact_email
    if body.contact_phone is not None:
        customer.contact_phone = body.contact_phone
    if body.status is not None:
        customer.status = body.status
    db.commit()
    db.refresh(customer)
    if body.status is not None and body.status != old_status:
        log_event(
            db,
            actor_id=admin.id,
            action=CUSTOMER_STATUS_CHANGED,
            entity_type="customer",
            entity_id=customer.id,
            meta={"old": old_status, "new": customer.status},
        )
    return customer


@router.post("/customers/{customer_id}/renew")
def renew_customer(
    customer_id: int,
    body: RenewalRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    """Renew a customer's subscription by one month.

    Honors the Idempotency-Key header: a repeated request with the same key
    returns the stored response instead of renewing again.
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")

    namespaced = f"renew:{customer_id}:{idempotency_key}" if idempotency_key else None
    stored: IdempotencyKey | None = None
    if namespaced:
        stored = (
            db.query(IdempotencyKey).filter(IdempotencyKey.key == namespaced).one_or_none()
        )
        if stored is not None and stored.response_status is not None:
            return JSONResponse(
                status_code=stored.response_status, content=stored.response_body
            )
        if stored is None:
            stored = IdempotencyKey(key=namespaced)
            db.add(stored)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="renewal already in progress for this idempotency key",
                ) from None
            db.refresh(stored)

    subscription = get_subscription(db, customer)
    if subscription is None:
        # First-time provisioning (exactly one month, no extra renewal month).
        subscription, renewal = provision_subscription(
            db, customer, operator_id=admin.id, reason=body.reason
        )
    else:
        renewal = renew_subscription(
            db, subscription, operator_id=admin.id, reason=body.reason
        )
    log_event(
        db,
        actor_id=admin.id,
        action=SUBSCRIPTION_RENEWED,
        entity_type="subscription",
        entity_id=subscription.id,
        meta={
            "customer_id": customer.id,
            "old_expiry": renewal.old_expiry.isoformat(),
            "new_expiry": renewal.new_expiry.isoformat(),
        },
    )
    payload = RenewalRead.model_validate(renewal).model_dump(mode="json")

    if stored is not None:
        stored.response_status = 200
        stored.response_body = payload
        db.commit()

    return JSONResponse(status_code=200, content=payload)


@router.get("/audit", response_model=list[AuditLogRead])
def list_audit(
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    actor_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(_admin_only),
) -> list[AuditLog]:
    query = db.query(AuditLog)
    if action is not None:
        query = query.filter(AuditLog.action == action)
    if entity_type is not None:
        query = query.filter(AuditLog.entity_type == entity_type)
    if actor_id is not None:
        query = query.filter(AuditLog.actor_id == actor_id)
    return (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

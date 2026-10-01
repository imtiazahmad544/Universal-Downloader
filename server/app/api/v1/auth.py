"""Auth endpoints: login (username or customer_code) and token refresh."""

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import rate_limit_login
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from app.models.models import ROLE_CUSTOMER, USER_ACTIVE, Customer, User
from app.schemas.schemas import LoginRequest, RefreshRequest, TokenPair
from app.services.audit import AUTH_LOGIN, AUTH_LOGIN_FAILED, AUTH_REFRESH, log_event

router = APIRouter()


def _find_login_user(db: Session, identifier: str) -> User | None:
    """Find the user for `identifier`: first by username, then by customer
    code (the customer login user has username == customer_code)."""
    user = db.query(User).filter(User.username == identifier).one_or_none()
    if user is not None:
        return user
    customer = (
        db.query(Customer).filter(Customer.customer_code == identifier).one_or_none()
    )
    if customer is None:
        return None
    user = (
        db.query(User)
        .filter(User.username == customer.customer_code, User.role == ROLE_CUSTOMER)
        .one_or_none()
    )
    if user is not None:
        return user
    return (
        db.query(User)
        .filter(
            User.customer_id == customer.id,
            User.role == ROLE_CUSTOMER,
            User.status == USER_ACTIVE,
        )
        .order_by(User.id)
        .first()
    )


@router.post("/login", response_model=TokenPair)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit_login),
) -> TokenPair:
    user = _find_login_user(db, body.identifier)
    if (
        user is None
        or user.status != USER_ACTIVE
        or not verify_password(body.password, user.password_hash)
    ):
        log_event(
            db,
            actor_id=None,
            action=AUTH_LOGIN_FAILED,
            entity_type="auth",
            entity_id=body.identifier,
        )
        raise HTTPException(status_code=401, detail="invalid credentials")
    access_token = create_access_token(
        user.id, user.role, user.customer_id, settings.ACCESS_TOKEN_MINUTES
    )
    refresh_token, _row = create_refresh_token(db, user.id)
    log_event(
        db,
        actor_id=user.id,
        action=AUTH_LOGIN,
        entity_type="user",
        entity_id=user.id,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        new_token, _new_hash, new_row = rotate_refresh_token(db, body.refresh_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid refresh token") from None
    user = db.query(User).filter(User.id == new_row.user_id).one_or_none()
    if user is None or user.status != USER_ACTIVE:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    log_event(
        db,
        actor_id=user.id,
        action=AUTH_REFRESH,
        entity_type="user",
        entity_id=user.id,
    )
    access_token = create_access_token(
        user.id, user.role, user.customer_id, settings.ACCESS_TOKEN_MINUTES
    )
    return TokenPair(access_token=access_token, refresh_token=new_token)

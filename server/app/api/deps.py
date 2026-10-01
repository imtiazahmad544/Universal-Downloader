"""Shared FastAPI dependencies: auth, roles, tenant scoping, rate limiting."""

import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db, utcnow
from app.core.security import decode_access_token
from app.models.models import (
    ROLE_CUSTOMER,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_EXPIRING_SOON,
    Customer,
    User,
)
from app.services.subscriptions import (
    effective_status,
    get_subscription,
    refresh_subscription_status,
)

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Decode the JWT bearer token and load the active user.

    401 when the token is missing, malformed, invalid, or expired, or when
    the user no longer exists. 403 when the user account is disabled.
    """
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    raw_id = payload.get("sub") or payload.get("user_id")
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid access token") from None
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="user account is disabled")
    return user


def require_roles(*roles: str):
    """Dependency factory: allow only users whose role is in `roles`.

    Returns 403 on mismatch.
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="insufficient role")
        return user

    return _check


def get_customer_user(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> tuple[User, Customer]:
    """Require a customer-role user and load their Customer row.

    The customer_id is always derived from the JWT user — never from client
    input. 404 when the customer row is missing.
    """
    if user.role != ROLE_CUSTOMER:
        raise HTTPException(status_code=403, detail="customer access required")
    customer = (
        db.query(Customer).filter(Customer.id == user.customer_id).one_or_none()
        if user.customer_id is not None
        else None
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return user, customer


def require_active_subscription(
    dep: tuple[User, Customer] = Depends(get_customer_user),
    db: Session = Depends(get_db),
) -> tuple[User, Customer]:
    """Gate customer write operations on an active (or expiring-soon)
    subscription. 403 {"detail": "subscription not active"} otherwise.

    Reads, pause, and status-reporting intentionally bypass this gate.
    """
    user, customer = dep
    subscription = get_subscription(db, customer)
    if subscription is None:
        raise HTTPException(status_code=403, detail="subscription not active")
    refresh_subscription_status(db, subscription)
    effective = effective_status(customer.status, subscription.expires_at, utcnow())
    if effective not in (SUBSCRIPTION_ACTIVE, SUBSCRIPTION_EXPIRING_SOON):
        raise HTTPException(status_code=403, detail="subscription not active")
    return user, customer


# ---------------------------------------------------------------------------
# Login rate limiting: in-memory sliding window per client IP.
# ---------------------------------------------------------------------------

_login_attempts: dict[str, deque[float]] = {}
_login_lock = threading.Lock()


def rate_limit_login(request: Request) -> None:
    """Allow settings.LOGIN_RATE_LIMIT_ATTEMPTS attempts per
    settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS per client IP; 429 when exceeded.

    Thread-safe; old entries are pruned on every check.
    """
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    max_attempts = settings.LOGIN_RATE_LIMIT_ATTEMPTS
    with _login_lock:
        bucket = _login_attempts.get(ip)
        if bucket is None:
            bucket = deque()
            _login_attempts[ip] = bucket
        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= max_attempts:
            raise HTTPException(
                status_code=429, detail="too many login attempts, try again later"
            )
        bucket.append(now)

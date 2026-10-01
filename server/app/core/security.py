"""Password hashing and JWT/refresh-token helpers.

Passwords: pwdlib PasswordHash with argon2id.
Access tokens: signed JWTs (PyJWT), short-lived.
Refresh tokens: opaque random tokens; only the SHA-256 hash is stored in the
database, so a DB leak does not yield usable tokens. Refresh tokens support
rotation (each use issues a new token and revokes the old one).
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import utcnow

_password_hasher = PasswordHash((Argon2Hasher(),))  # argon2id (default Type.ID)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against its stored hash."""
    try:
        return bool(_password_hasher.verify(password, password_hash))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: int,
    role: str,
    customer_id: int | None,
    expires_minutes: int | None = None,
) -> str:
    """Create a short-lived signed JWT access token."""
    # NOTE: aware datetime is required here. .timestamp() on a naive datetime is
    # interpreted in the process-local timezone (TZ), so on any non-UTC host the
    # iat/exp claims would silently shift by the UTC offset (tokens born
    # expired). DB storage stays naive UTC via utcnow() elsewhere.
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "user_id": user_id,
        "role": role,
        "customer_id": customer_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token. Raises ValueError on any problem."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"invalid access token: {exc}") from exc


# ---------------------------------------------------------------------------
# Refresh tokens (opaque, hashed at rest)
# ---------------------------------------------------------------------------


def generate_refresh_token() -> tuple[str, str]:
    """Generate an opaque refresh token and its SHA-256 storage hash."""
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, token_hash


def _find_valid_token(db: Session, token: str):
    """Return the RefreshToken row for `token` if usable, else None."""
    from app.models.models import RefreshToken

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .one_or_none()
    )
    if row is None or row.revoked:
        return None
    if row.expires_at <= utcnow():
        return None
    return row


def create_refresh_token(db: Session, user_id: int) -> tuple[str, "RefreshTokenRow"]:
    """Persist a new refresh token for `user_id`. Returns (opaque_token, row)."""
    from app.models.models import RefreshToken

    token, token_hash = generate_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        revoked=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def verify_refresh_token(db: Session, token: str):
    """Verify an opaque refresh token: hash match, not revoked, not expired."""
    return _find_valid_token(db, token)


def rotate_refresh_token(db: Session, token: str) -> tuple[str, str, "RefreshTokenRow"]:
    """Rotate a refresh token: revoke the old one, issue a new one (atomic).

    Returns (new_opaque_token, new_token_hash, new_row). The new row records
    `rotated_from_id` pointing at the revoked predecessor.
    Raises ValueError if the presented token is invalid/expired/revoked.
    """
    from app.models.models import RefreshToken

    old = _find_valid_token(db, token)
    if old is None:
        raise ValueError("refresh token is invalid, expired, or revoked")
    new_token, new_hash = generate_refresh_token()
    old.revoked = True
    new_row = RefreshToken(
        user_id=old.user_id,
        token_hash=new_hash,
        expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        revoked=False,
        rotated_from_id=old.id,
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return new_token, new_hash, new_row


def revoke_refresh_token(db: Session, token: str) -> bool:
    """Revoke the refresh token, if it exists. Returns True when revoked."""
    row = _find_valid_token(db, token)
    if row is None:
        return False
    row.revoked = True
    db.commit()
    return True


# Type alias used in annotations (imported lazily to avoid circulars at import
# time); kept here for readability of the helper signatures.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.models import RefreshToken as RefreshTokenRow

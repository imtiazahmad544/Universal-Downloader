"""Encrypted cookies (v2.0, optional feature).

Customers may upload their own browser cookies (Netscape format) so that
extraction/download can get past login walls and bot checks. Cookies are
Fernet-encrypted at rest:

- customers.cookies_encrypted ......... global fallback for all sources
- sources.cookies_encrypted ........... per-source override (wins)

Resolution order: source override first, then the customer's global cookies.

Security rules (hard):
- Raw cookie values are NEVER logged and NEVER returned by any GET endpoint.
- The encryption key comes from COOKIES_ENCRYPTION_KEY. When it is unset,
  dev gets a LOUD warning and an ephemeral key (cookies die on restart);
  prod (APP_ENV=prod/production) refuses to start the crypto path at all.
"""

import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.core.database import utcnow
from app.models.models import Customer, Source

logger = structlog.get_logger("universal-downloader.cookies")

NETSCAPE_HEADER = "# Netscape HTTP Cookie File"

# Max accepted upload size (256 KiB): cookies files are tiny; anything bigger
# is almost certainly not a cookies file.
MAX_COOKIES_BYTES = 256 * 1024

_dev_ephemeral_key: bytes | None = None


def _fernet() -> Fernet:
    """Resolve the Fernet instance for cookie encryption.

    Raises RuntimeError when COOKIES_ENCRYPTION_KEY is missing in prod or
    when the configured value is not a valid Fernet key.
    """
    global _dev_ephemeral_key
    raw = (settings.COOKIES_ENCRYPTION_KEY or "").strip()
    if raw:
        try:
            return Fernet(raw.encode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"COOKIES_ENCRYPTION_KEY is not a valid Fernet key: {exc}"
            ) from exc
    if settings.APP_ENV.lower() in ("prod", "production"):
        raise RuntimeError(
            "COOKIES_ENCRYPTION_KEY must be set in production (APP_ENV="
            f"{settings.APP_ENV!r}); refusing to encrypt cookies with a "
            "throwaway key."
        )
    if _dev_ephemeral_key is None:
        _dev_ephemeral_key = Fernet.generate_key()
        message = (
            "COOKIES_ENCRYPTION_KEY is unset: using an EPHEMERAL dev key. "
            "Stored cookies will become UNREADABLE after every restart. "
            "Set a persistent COOKIES_ENCRYPTION_KEY before production use."
        )
        logger.warning("cookies.dev_ephemeral_key", warning=message)
        # Loud on purpose: this must be impossible to miss in server logs.
        print(f"WARNING: {message}")
    return Fernet(_dev_ephemeral_key)


def validate_cookies_format(raw_text: str) -> None:
    """Loosely validate Netscape cookies text. Raises ValueError when the
    text is empty or is not a recognized cookies format.

    Accepted: the '# Netscape HTTP Cookie File' header, or tab-separated
    cookie lines (the 7-field Netscape layout). Comment/blank lines are
    ignored; validation is deliberately loose — browsers export minor
    variants.
    """
    text = raw_text or ""
    if not text.strip():
        raise ValueError("cookies file is empty")
    lines = text.splitlines()
    has_header = any(
        line.strip().lower().startswith("# netscape http cookie file")
        for line in lines
    )
    data_lines = [
        line for line in lines if line.strip() and not line.strip().startswith("#")
    ]
    has_tabbed_lines = any("\t" in line for line in data_lines)
    if not (has_header or has_tabbed_lines):
        raise ValueError(
            "not a recognized cookies format: expected the "
            "'# Netscape HTTP Cookie File' header or tab-separated cookie lines"
        )


def encrypt_cookies(raw_text: str) -> str:
    """Validate and Fernet-encrypt raw Netscape cookies text.

    Returns the encrypted blob (str). Raises ValueError on bad format,
    RuntimeError on key misconfiguration.
    """
    validate_cookies_format(raw_text)
    token = _fernet().encrypt(raw_text.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_cookies(blob: str) -> str:
    """Decrypt a blob produced by encrypt_cookies(). Raises ValueError when
    the blob cannot be decrypted (wrong key or corrupted data)."""
    if not blob:
        raise ValueError("no cookies stored")
    try:
        return _fernet().decrypt(blob.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "could not decrypt cookies (wrong key or corrupted data)"
        ) from exc


def resolve_cookies_for_source(
    source: Source, db: Session | None = None
) -> str | None:
    """Return the raw Netscape cookies text for a source, or None.

    Precedence: the source's own cookies override the customer's global
    cookies. Never logs the values — only presence and byte length.
    """
    if source.cookies_encrypted:
        raw = decrypt_cookies(source.cookies_encrypted)
        logger.debug(
            "cookies.resolved",
            source_id=source.id,
            scope="source",
            bytes=len(raw),
        )
        return raw
    session = db
    if session is None:
        # object_session returns None for transient/detached instances.
        session = object_session(source)
    if session is None:
        logger.warning(
            "cookies.no_session_for_customer_lookup", source_id=source.id
        )
        return None
    customer = session.get(Customer, source.customer_id)
    if customer is None or not customer.cookies_encrypted:
        return None
    raw = decrypt_cookies(customer.cookies_encrypted)
    logger.debug(
        "cookies.resolved",
        source_id=source.id,
        scope="customer",
        bytes=len(raw),
    )
    return raw


def store_source_cookies(db: Session, source: Source, raw_text: str) -> None:
    """Encrypt and store per-source cookies. Caller commits."""
    source.cookies_encrypted = encrypt_cookies(raw_text)
    source.cookies_updated_at = utcnow()


def clear_source_cookies(db: Session, source: Source) -> None:  # noqa: ARG001
    """Remove per-source cookies. Caller commits."""
    source.cookies_encrypted = None
    source.cookies_updated_at = None


def store_customer_cookies(db: Session, customer: Customer, raw_text: str) -> None:
    """Encrypt and store the customer's global cookies. Caller commits."""
    customer.cookies_encrypted = encrypt_cookies(raw_text)
    customer.cookies_updated_at = utcnow()


def clear_customer_cookies(db: Session, customer: Customer) -> None:  # noqa: ARG001
    """Remove the customer's global cookies. Caller commits."""
    customer.cookies_encrypted = None
    customer.cookies_updated_at = None


def cookies_status(entity: Source | Customer) -> dict:
    """Presence-only status for API responses. Raw values never leave here."""
    return {
        "present": bool(entity.cookies_encrypted),
        "updated_at": entity.cookies_updated_at,
    }

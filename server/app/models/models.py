"""SQLAlchemy 2.0 models for the Universal Downloader backend.

Conventions:
- SQLAlchemy 2.0 style only: DeclarativeBase + mapped_column. No Postgres-only
  column types; every status/type column is a String with module-level
  constants (never sqlalchemy.Enum) so the schema ports SQLite <-> Postgres.
- Datetime convention: naive UTC everywhere (see app.core.database). All
  DateTime defaults use utcnow(); services compare against utcnow().
- Table/column names are snake_case.
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, utcnow

# ---------------------------------------------------------------------------
# Status / type constants (all String columns; never sqlalchemy.Enum)
# ---------------------------------------------------------------------------

# Customer.status
CUSTOMER_ACTIVE = "active"
CUSTOMER_SUSPENDED = "suspended"
CUSTOMER_DISABLED = "disabled"

# Subscription.status (maintained by the subscription service)
SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_EXPIRING_SOON = "expiring_soon"
SUBSCRIPTION_EXPIRED = "expired"

# User.role
ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_CUSTOMER = "customer"

# User.status
USER_ACTIVE = "active"
USER_DISABLED = "disabled"

# Source.platform
PLATFORM_TIKTOK = "tiktok"
PLATFORM_INSTAGRAM = "instagram"
PLATFORM_YOUTUBE = "youtube"

# Source.status
SOURCE_ACTIVE = "active"
SOURCE_PAUSED = "paused"
SOURCE_REMOVED = "removed"

# DiscoveryRun.status
DISCOVERY_PENDING = "pending"
DISCOVERY_RUNNING = "running"
DISCOVERY_SUCCESS = "success"
DISCOVERY_PARTIAL = "partial"
DISCOVERY_FAILED = "failed"

# MediaItem.status
MEDIA_DISCOVERED = "discovered"
MEDIA_INVALID = "invalid"
MEDIA_DUPLICATE = "duplicate"

# Batch.status
BATCH_OPEN = "open"
BATCH_ACTIVE = "active"
BATCH_DONE = "done"

# ProviderEvent.event_type
EVENT_SELECTED = "selected"
EVENT_FALLBACK = "fallback"
EVENT_TIMEOUT = "timeout"
EVENT_RATE_LIMITED = "rate_limited"
EVENT_UNSUPPORTED = "unsupported"
EVENT_ERROR = "error"
EVENT_SUCCESS = "success"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=CUSTOMER_ACTIVE)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timezone: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # IANA timezone for batch scheduling; null -> server default (UTC)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    users: Mapped[list["User"]] = relationship(back_populates="customer")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="customer")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), unique=True, nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SUBSCRIPTION_ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    customer: Mapped["Customer"] = relationship(back_populates="subscription")


class SubscriptionRenewal(Base):
    """Immutable ledger of subscription renewals — there is no update or
    delete path; history is append-only."""

    __tablename__ = "subscription_renewals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id"), nullable=False
    )
    operator_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )  # null = system
    old_expiry: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    new_expiry: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    renewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class User(Base):
    """Login user. Convention: a customer's login user has
    username == customer_code. customer_id is NULL for admin/super_admin."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=USER_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    customer: Mapped["Customer | None"] = relationship(back_populates="users")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("customer_id", "canonical_id", name="uq_sources_customer_canonical"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    input_value: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=SOURCE_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DISCOVERY_PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint("customer_id", "canonical_url", name="uq_media_customer_url"),
        Index("ix_media_items_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False
    )  # denormalized for tenant isolation
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id"), nullable=False
    )
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MEDIA_DISCOVERED
    )


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("customer_id", "batch_date", name="uq_batches_customer_date"),
        Index("ix_batches_customer_status", "customer_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False
    )
    batch_date: Mapped[date] = mapped_column(Date, nullable=False)  # local date in customer's timezone
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=BATCH_OPEN)


class DownloadJob(Base):
    """Job status follows the 14-state machine in app.services.state_machine."""

    __tablename__ = "download_jobs"
    __table_args__ = (
        Index("ix_download_jobs_customer_status", "customer_id", "status"),
        Index("ix_download_jobs_batch_status", "batch_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False
    )
    media_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_items.id"), nullable=False
    )
    batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("batches.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="discovered")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    progress: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # 0-100 download progress percent, reported by the Windows client
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class DownloadFile(Base):
    __tablename__ = "download_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("download_jobs.id"), unique=True, nullable=False
    )
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    __table_args__ = (Index("ix_provider_events_job_id", "job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("download_jobs.id"), nullable=True
    )
    discovery_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("discovery_runs.id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )  # null = system
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # NOTE: attribute MUST be `meta` (column name "metadata") because
    # `metadata` is reserved on DeclarativeBase.
    meta = Column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class RefreshToken(Base):
    """Gap-fill table for rotating opaque refresh tokens. Only the SHA-256
    hash of the token is stored; the opaque value is shown once at issue."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    rotated_from_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("refresh_tokens.id"), nullable=True
    )

    user: Mapped["User"] = relationship()


__all__ = [
    "Customer",
    "Subscription",
    "SubscriptionRenewal",
    "User",
    "Source",
    "DiscoveryRun",
    "MediaItem",
    "Batch",
    "DownloadJob",
    "DownloadFile",
    "ProviderEvent",
    "AuditLog",
    "RefreshToken",
    "CUSTOMER_ACTIVE",
    "CUSTOMER_SUSPENDED",
    "CUSTOMER_DISABLED",
    "SUBSCRIPTION_ACTIVE",
    "SUBSCRIPTION_EXPIRING_SOON",
    "SUBSCRIPTION_EXPIRED",
    "ROLE_SUPER_ADMIN",
    "ROLE_ADMIN",
    "ROLE_CUSTOMER",
    "USER_ACTIVE",
    "USER_DISABLED",
    "PLATFORM_TIKTOK",
    "PLATFORM_INSTAGRAM",
    "PLATFORM_YOUTUBE",
    "SOURCE_ACTIVE",
    "SOURCE_PAUSED",
    "SOURCE_REMOVED",
    "DISCOVERY_PENDING",
    "DISCOVERY_RUNNING",
    "DISCOVERY_SUCCESS",
    "DISCOVERY_PARTIAL",
    "DISCOVERY_FAILED",
    "MEDIA_DISCOVERED",
    "MEDIA_INVALID",
    "MEDIA_DUPLICATE",
    "BATCH_OPEN",
    "BATCH_ACTIVE",
    "BATCH_DONE",
    "EVENT_SELECTED",
    "EVENT_FALLBACK",
    "EVENT_TIMEOUT",
    "EVENT_RATE_LIMITED",
    "EVENT_UNSUPPORTED",
    "EVENT_ERROR",
    "EVENT_SUCCESS",
]


class IdempotencyKey(Base):
    """Gap-fill (v1.1): durable idempotency keys for retried admin operations
    (e.g. POST /admin/customers/{id}/renew)."""

    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)

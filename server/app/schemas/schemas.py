"""Pydantic v2 request/response schemas for the Universal Downloader API."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------


class CustomerCreate(BaseModel):
    customer_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=64)


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)


class CustomerRead(_ReadModel):
    id: int
    customer_code: str
    name: str
    contact_email: str | None
    contact_phone: str | None
    status: str
    daily_start_time: str = "00:00"  # v2.0: local "HH:MM" batch window start
    created_at: datetime
    updated_at: datetime


class CustomerSettingsUpdate(BaseModel):
    """PATCH /me/settings body (v2.0)."""

    # Plain str (no length constraints): validate_daily_start_time() in the
    # endpoint rejects every malformed value with a uniform 400.
    daily_start_time: str


class CookiesStatusRead(BaseModel):
    """v2.0: presence-only cookies status. Raw cookie values are NEVER
    returned by any endpoint."""

    present: bool
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


class SubscriptionRead(_ReadModel):
    id: int
    customer_id: int
    starts_at: datetime
    expires_at: datetime
    status: str
    effective_status: str
    days_remaining: int


class RenewalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


class RenewalRead(_ReadModel):
    id: int
    subscription_id: int
    operator_id: int | None
    old_expiry: datetime
    new_expiry: datetime
    renewed_at: datetime
    reason: str | None


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserRead(_ReadModel):
    id: int
    customer_id: int | None
    role: str
    username: str
    status: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class SourceCreate(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    input_value: str = Field(min_length=1, max_length=1024)


class SourceUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=32)


class SourceRead(_ReadModel):
    id: int
    customer_id: int
    platform: str
    input_value: str
    canonical_id: str
    status: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class DiscoveryRunRead(_ReadModel):
    id: int
    source_id: int
    provider: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    items_found: int
    error: str | None


class MediaItemRead(_ReadModel):
    id: int
    customer_id: int
    source_id: int
    canonical_url: str
    external_id: str | None
    discovered_at: datetime
    status: str


# ---------------------------------------------------------------------------
# Batch / Job / File
# ---------------------------------------------------------------------------


class BatchRead(_ReadModel):
    id: int
    customer_id: int
    batch_date: date
    timezone: str
    size: int
    status: str


class JobRead(_ReadModel):
    id: int
    customer_id: int
    media_item_id: int
    batch_id: int | None
    status: str
    attempts: int
    progress: float = 0
    provider: str | None
    # v2.0: captcha flow + extraction strategy tracking.
    captcha_required: bool = False
    extraction_strategy: str | None = None
    last_error_code: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("progress", mode="before")
    @classmethod
    def _coerce_progress(cls, v):
        # The column is nullable for pre-gap-fill rows; the API contract
        # (and the Windows client) treats progress as a non-null 0-100 float.
        return 0.0 if v is None else v


class JobStatusUpdate(BaseModel):
    """Body for PATCH /jobs/{id}/status (used by a later API workstream)."""

    status: str = Field(min_length=1, max_length=64)
    # 0-100 download progress percent, reported by the Windows client.
    # Range-checked in the endpoint (400 on violation), not here, so the
    # endpoint's 400 behavior is reachable.
    progress: float | None = Field(default=None)
    file_path: str | None = Field(default=None, max_length=2048)
    file_size: int | None = Field(default=None, ge=0)
    checksum: str | None = Field(default=None, max_length=128)
    error: str | None = Field(default=None, max_length=2048)


class FileRead(_ReadModel):
    id: int
    job_id: int
    path: str
    size: int | None
    checksum: str | None
    completed_at: datetime | None


# ---------------------------------------------------------------------------
# Provider events / audit
# ---------------------------------------------------------------------------


class ProviderEventRead(_ReadModel):
    id: int
    job_id: int | None
    discovery_run_id: int | None
    provider: str
    event_type: str
    details: dict | None
    created_at: datetime


class AuditLogRead(_ReadModel):
    id: int
    actor_id: int | None
    action: str
    entity_type: str
    entity_id: str
    meta: dict | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Link extractor (v2.0)
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ExtractResponse(BaseModel):
    ok: bool
    media_url: str | None = None
    title: str | None = None
    ext: str | None = None
    strategy: str | None = None
    error: str | None = None
    captcha_required: bool = False


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


__all__ = [
    "CustomerCreate",
    "CustomerUpdate",
    "CustomerRead",
    "CustomerSettingsUpdate",
    "CookiesStatusRead",
    "SubscriptionRead",
    "RenewalRequest",
    "RenewalRead",
    "UserRead",
    "SourceCreate",
    "SourceUpdate",
    "SourceRead",
    "DiscoveryRunRead",
    "MediaItemRead",
    "BatchRead",
    "JobRead",
    "JobStatusUpdate",
    "FileRead",
    "ProviderEventRead",
    "AuditLogRead",
    "ExtractRequest",
    "ExtractResponse",
    "LoginRequest",
    "TokenPair",
    "RefreshRequest",
]

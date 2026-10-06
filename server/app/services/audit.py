"""Audit logging: append-only record of security- and business-relevant events."""

from sqlalchemy.orm import Session

from app.models.models import AuditLog

# Action constants — use these everywhere instead of string literals.
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_REFRESH = "auth.refresh"
CUSTOMER_CREATED = "customer.created"
CUSTOMER_UPDATED = "customer.updated"
CUSTOMER_STATUS_CHANGED = "customer.status_changed"
SUBSCRIPTION_RENEWED = "subscription.renewed"
SOURCE_CREATED = "source.created"
SOURCE_UPDATED = "source.updated"
SOURCE_REMOVED = "source.removed"
DISCOVERY_REQUESTED = "discovery.requested"
DISCOVERY_COMPLETED = "discovery.completed"
PROVIDER_SELECTED = "provider.selected"
PROVIDER_FALLBACK = "provider.fallback"
JOB_CREATED = "job.created"
JOB_STATUS_CHANGED = "job.status_changed"
JOB_RETRIED = "job.retried"
JOB_PAUSED = "job.paused"
JOB_RESUMED = "job.resumed"
JOB_CAPTCHA = "job.captcha"  # v2.0: job parked on a captcha/bot-check wall
BATCH_CREATED = "batch.created"
COOKIES_UPDATED = "cookies.updated"  # v2.0: cookies uploaded (never the values)
COOKIES_DELETED = "cookies.deleted"  # v2.0
CUSTOMER_SETTINGS_UPDATED = "customer.settings_updated"  # v2.0


def log_event(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str,
    meta: dict | None = None,
) -> AuditLog:
    """Append one audit row (actor_id=None means the system)."""
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        meta=meta,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

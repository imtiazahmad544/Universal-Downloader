"""v2.0: encrypted cookies, captcha flow, custom daily start time, scheduler run tracking."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_v2_0"
down_revision: str | None = "0002_gapfills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Encrypted cookies (global per customer, override per source). The
    # values are Fernet-encrypted blobs; the API never returns raw cookies.
    op.add_column("customers", sa.Column("cookies_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "customers", sa.Column("cookies_updated_at", sa.DateTime(), nullable=True)
    )
    # Custom daily batch window start ("HH:MM"). server_default keeps the
    # v1.1 midnight behavior for existing rows.
    op.add_column(
        "customers",
        sa.Column(
            "daily_start_time",
            sa.String(5),
            nullable=False,
            server_default="00:00",
        ),
    )
    op.add_column(
        "customers", sa.Column("last_scheduler_run_at", sa.DateTime(), nullable=True)
    )

    op.add_column("sources", sa.Column("cookies_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "sources", sa.Column("cookies_updated_at", sa.DateTime(), nullable=True)
    )

    # Captcha flow + extraction strategy tracking on jobs.
    # server_default "0" is a boolean-false literal on both SQLite and Postgres.
    op.add_column(
        "download_jobs",
        sa.Column(
            "captcha_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "download_jobs", sa.Column("extraction_strategy", sa.String(64), nullable=True)
    )
    op.add_column(
        "download_jobs", sa.Column("last_error_code", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("download_jobs", "last_error_code")
    op.drop_column("download_jobs", "extraction_strategy")
    op.drop_column("download_jobs", "captcha_required")
    op.drop_column("sources", "cookies_updated_at")
    op.drop_column("sources", "cookies_encrypted")
    op.drop_column("customers", "last_scheduler_run_at")
    op.drop_column("customers", "daily_start_time")
    op.drop_column("customers", "cookies_updated_at")
    op.drop_column("customers", "cookies_encrypted")

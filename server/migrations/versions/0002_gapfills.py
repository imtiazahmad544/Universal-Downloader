"""v1.1 gap-fills: customers.timezone, download_jobs.progress, idempotency_keys table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_gapfills"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers", sa.Column("timezone", sa.String(64), nullable=True)
    )
    op.add_column(
        "download_jobs", sa.Column("progress", sa.Float(), nullable=True)
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_column("download_jobs", "progress")
    op.drop_column("customers", "timezone")

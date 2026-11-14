"""Add the import batch ledger used by CSV import and reconciliation."""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_import_batches"
down_revision: Union[str, None] = "0002_tenant_scoped_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "import_batches" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, server_default=sa.text("'demo'")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accepted_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rejected_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "accepted_rows >= 0 AND skipped_rows >= 0 AND rejected_rows >= 0",
            name="ck_import_batches_row_counts",
        ),
        sa.Index("ix_import_batches_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_import_batches_sha", "tenant_id", "kind", "content_sha256"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "import_batches" in sa.inspect(bind).get_table_names():
        op.drop_table("import_batches")

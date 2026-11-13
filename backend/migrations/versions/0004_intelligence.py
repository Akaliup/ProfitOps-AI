"""Add immutable import provenance and weekly report snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0004_intelligence"
down_revision = "0003_import_batches"
branch_labels = depends_on = None


def upgrade():
    # Freeze table definitions here rather than importing mutable application models.
    metadata = sa.MetaData()
    for name in ("stores", "products", "import_batches"):
        sa.Table(name, metadata, sa.Column("id", sa.String, primary_key=True))
    source = sa.Table("source_records", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("natural_key", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("store_id", sa.String(40), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("product_id", sa.String(40), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.UniqueConstraint("tenant_id", "kind", "natural_key", name="uq_source_natural"))
    weekly = sa.Table("weekly_reports", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("body", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    source.create(op.get_bind(), checkfirst=True)
    weekly.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("weekly_reports")
    op.drop_table("source_records")

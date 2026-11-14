"""Persist conversations separately from immutable weekly snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0005_weekly_chat"
down_revision = "0004_intelligence"
branch_labels = depends_on = None


def upgrade():
    metadata = sa.MetaData()
    sa.Table("weekly_reports", metadata, sa.Column("id", sa.String(36), primary_key=True))
    threads = sa.Table("weekly_chat_threads", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("weekly_reports.id"), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "report_id", name="uq_weekly_chat_thread_report"))
    messages = sa.Table("weekly_chat_messages", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("thread_id", sa.String(36), sa.ForeignKey("weekly_chat_threads.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("status", sa.String(40)),
        sa.Column("reason", sa.String(80)),
        sa.Column("claims", sa.JSON, nullable=False),
        sa.Column("trace", sa.JSON, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_weekly_chat_role"),
        sa.Index("ix_weekly_chat_time", "tenant_id", "thread_id", "created_at"))
    threads.create(op.get_bind(), checkfirst=True)
    messages.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("weekly_chat_messages")
    op.drop_table("weekly_chat_threads")

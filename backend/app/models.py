from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, and_, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# Partial unique index: one active document per content hash, while allowing
# any number of soft-deleted historical rows with the same content.
KNOWLEDGE_ACTIVE_SHA_INDEX = "uq_knowledge_documents_tenant_active_sha"


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String(100))


class SeedVersion(Base):
    __tablename__ = "seed_versions"
    version: Mapped[str] = mapped_column(String(50), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_email", "email"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    name: Mapped[str] = mapped_column(String(100))
    marketplace: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
    )
    sku: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("sales >= 0 AND discounts >= 0 AND refunds >= 0"),
        CheckConstraint("cogs >= 0 AND fulfillment >= 0 AND platform_fees >= 0 AND other_costs >= 0"),
        CheckConstraint("quantity > 0"),
        Index("ix_orders_date_store", "date", "store_id"),
        Index("ix_orders_tenant_date_store", "tenant_id", "date", "store_id"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo")
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    sales: Mapped[int] = mapped_column(BigInteger)
    discounts: Mapped[int] = mapped_column(BigInteger, default=0)
    refunds: Mapped[int] = mapped_column(BigInteger, default=0)
    cogs: Mapped[int] = mapped_column(BigInteger)
    fulfillment: Mapped[int] = mapped_column(BigInteger)
    platform_fees: Mapped[int] = mapped_column(BigInteger)
    other_costs: Mapped[int] = mapped_column(BigInteger)


class DailyOperation(Base):
    __tablename__ = "daily_operations"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", "date", name="uq_daily_scope"),
        CheckConstraint("ad_spend >= 0 AND inventory >= 0 AND inbound_inventory >= 0"),
        Index("ix_operations_date_store", "date", "store_id"),
        Index("ix_operations_tenant_date_store", "tenant_id", "date", "store_id"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo")
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    date: Mapped[date] = mapped_column(Date)
    ad_spend: Mapped[int] = mapped_column(BigInteger)
    inventory: Mapped[int] = mapped_column(Integer)
    inbound_inventory: Mapped[int] = mapped_column(Integer)
    complete: Mapped[bool] = mapped_column(Boolean, default=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        Index("ix_import_batches_tenant_created", "tenant_id", "created_at"),
        Index("ix_import_batches_sha", "tenant_id", "kind", "content_sha256"),
        CheckConstraint("accepted_rows >= 0 AND skipped_rows >= 0 AND rejected_rows >= 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo")
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="committed")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_active", "active"),
        Index("ix_knowledge_documents_sha", "content_sha256"),
        Index(
            KNOWLEDGE_ACTIVE_SHA_INDEX,
            "tenant_id",
            "content_sha256",
            unique=True,
            sqlite_where=text("active = 1"),
            postgresql_where=text("active = true"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(40), default="v1")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_index"),
        Index("ix_knowledge_chunks_document", "document_id"),
        Index("ix_knowledge_chunks_model", "embedding_model"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_analyses_tenant_idempotency"),
        Index("ix_analyses_status_next_run", "status", "next_run_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    # 100 client characters plus a tenant prefix (40 + ":") still fits.
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(80))
    request_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Durable job fields. A queued row is claimable once next_run_at is due;
    # a running row is owned by worker_id until lease_until expires.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def lease_is_live(now: datetime):
    """SQL predicate: this analysis still holds a non-expired worker lease.

    The comparison runs in the database. SQLite returns naive datetimes while
    ``now`` is timezone-aware, so evaluating the lease in Python would either
    raise or disagree with the stored value. A NULL ``lease_until`` means the
    row was never claimed or its lease was already cleared; neither is owned.
    """
    return and_(Analysis.lease_until.is_not(None), Analysis.lease_until > now)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','pending_approval','approved','executing','executed','rejected','execution_failed')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[dict] = mapped_column(JSON)
    action: Mapped[dict] = mapped_column(JSON)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_executions_tenant_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), unique=True)
    # 100 client characters plus a tenant prefix (40 + ":") still fits.
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    simulation: Mapped[bool] = mapped_column(Boolean, default=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(80))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_object_time", "object_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), default="demo", server_default="demo", index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceRecord(Base):
    """Validated source rows and immutable lineage; amounts in payload are cents."""
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "natural_key", name="uq_source_natural"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    natural_key: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"))
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    row_number: Mapped[int] = mapped_column(Integer)


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WeeklyChatThread(Base):
    __tablename__ = "weekly_chat_threads"
    __table_args__ = (UniqueConstraint("tenant_id", "report_id", name="uq_weekly_chat_thread_report"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("weekly_reports.id"))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WeeklyChatMessage(Base):
    __tablename__ = "weekly_chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_weekly_chat_role"),
        Index("ix_weekly_chat_time", "tenant_id", "thread_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("weekly_chat_threads.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(80))
    claims: Mapped[list] = mapped_column(JSON, default=list)
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

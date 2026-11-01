from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


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


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    marketplace: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("sales >= 0 AND discounts >= 0 AND refunds >= 0"),
        CheckConstraint("cogs >= 0 AND fulfillment >= 0 AND platform_fees >= 0 AND other_costs >= 0"),
        CheckConstraint("quantity > 0"),
        Index("ix_orders_date_store", "date", "store_id"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
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
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    date: Mapped[date] = mapped_column(Date)
    ad_spend: Mapped[int] = mapped_column(BigInteger)
    inventory: Mapped[int] = mapped_column(Integer)
    inbound_inventory: Mapped[int] = mapped_column(Integer)
    complete: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_active", "active"),
        Index("ix_knowledge_documents_sha", "content_sha256"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    request: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(80))
    request_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','pending_approval','approved','executing','executed','rejected','execution_failed')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
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
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

"""The real pre-tenancy schema, as shipped before commit 0f21732.

Migration tests build their database from these explicit Core tables rather
than importing the current models: importing the models would pre-create
``tenant_id``, ``attempts``, ``lease_until`` and the other columns the
migration is supposed to add.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Column, Date, DateTime,
    ForeignKey, Index, Integer, MetaData, String, Table, Text, UniqueConstraint,
    text,
)

LEGACY_ACTIVE_SHA_INDEX = "uq_knowledge_documents_active_sha"


def legacy_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "schema_versions", metadata,
        Column("version", Integer, primary_key=True),
        Column("description", String(100), nullable=False),
    )
    Table(
        "seed_versions", metadata,
        Column("version", String(50), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "stores", metadata,
        Column("id", String(40), primary_key=True),
        Column("name", String(100), nullable=False),
        Column("marketplace", String(100), nullable=False),
        Column("currency", String(3), nullable=False),
    )
    Table(
        "products", metadata,
        Column("id", String(40), primary_key=True),
        Column("sku", String(50), nullable=False, unique=True),
        Column("name", String(100), nullable=False),
    )
    Table(
        "orders", metadata,
        Column("id", String(80), primary_key=True),
        Column("store_id", String(40), ForeignKey("stores.id"), nullable=False),
        Column("product_id", String(40), ForeignKey("products.id"), nullable=False),
        Column("date", Date, nullable=False),
        Column("quantity", Integer, nullable=False),
        Column("sales", BigInteger, nullable=False),
        Column("discounts", BigInteger, nullable=False),
        Column("refunds", BigInteger, nullable=False),
        Column("cogs", BigInteger, nullable=False),
        Column("fulfillment", BigInteger, nullable=False),
        Column("platform_fees", BigInteger, nullable=False),
        Column("other_costs", BigInteger, nullable=False),
        CheckConstraint("sales >= 0 AND discounts >= 0 AND refunds >= 0"),
        CheckConstraint("cogs >= 0 AND fulfillment >= 0 AND platform_fees >= 0 AND other_costs >= 0"),
        CheckConstraint("quantity > 0"),
        Index("ix_orders_date_store", "date", "store_id"),
    )
    Table(
        "daily_operations", metadata,
        Column("id", String(80), primary_key=True),
        Column("store_id", String(40), ForeignKey("stores.id"), nullable=False),
        Column("product_id", String(40), ForeignKey("products.id"), nullable=False),
        Column("date", Date, nullable=False),
        Column("ad_spend", BigInteger, nullable=False),
        Column("inventory", Integer, nullable=False),
        Column("inbound_inventory", Integer, nullable=False),
        Column("complete", Boolean, nullable=False),
        UniqueConstraint("store_id", "product_id", "date", name="uq_daily_scope"),
        CheckConstraint("ad_spend >= 0 AND inventory >= 0 AND inbound_inventory >= 0"),
        Index("ix_operations_date_store", "date", "store_id"),
    )
    Table(
        "knowledge_documents", metadata,
        Column("id", String(36), primary_key=True),
        Column("title", String(200), nullable=False),
        Column("source", String(300), nullable=False),
        Column("version", String(40), nullable=False),
        Column("effective_date", Date, nullable=True),
        Column("content", Text, nullable=False),
        Column("content_sha256", String(64), nullable=False),
        Column("metadata_json", JSON, nullable=False),
        Column("active", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Index("ix_knowledge_documents_active", "active"),
        Index("ix_knowledge_documents_sha", "content_sha256"),
        Index(
            LEGACY_ACTIVE_SHA_INDEX, "content_sha256", unique=True,
            sqlite_where=text("active = 1"),
            postgresql_where=text("active = true"),
        ),
    )
    Table(
        "knowledge_chunks", metadata,
        Column("id", String(36), primary_key=True),
        Column("document_id", String(36), ForeignKey("knowledge_documents.id"), nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("heading", String(200), nullable=True),
        Column("content", Text, nullable=False),
        Column("char_count", Integer, nullable=False),
        Column("embedding", JSON, nullable=False),
        Column("embedding_model", String(80), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Index("uq_knowledge_chunk_index", "document_id", "chunk_index", unique=True),
        Index("ix_knowledge_chunks_document", "document_id"),
        Index("ix_knowledge_chunks_model", "embedding_model"),
    )
    Table(
        "analyses", metadata,
        Column("id", String(36), primary_key=True),
        Column("idempotency_key", String(100), nullable=False, unique=True),
        Column("request", JSON, nullable=False),
        Column("actor", String(80), nullable=False),
        Column("request_id", String(64), nullable=False),
        Column("status", String(30), nullable=False),
        Column("steps", JSON, nullable=False),
        Column("snapshot", JSON, nullable=False),
        Column("result", JSON, nullable=True),
        Column("error", JSON, nullable=True),
        Column("recommendation_id", String(36), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "recommendations", metadata,
        Column("id", String(36), primary_key=True),
        Column("analysis_id", String(36), ForeignKey("analyses.id"), nullable=False, unique=True),
        Column("status", String(30), nullable=False),
        Column("title", String(200), nullable=False),
        Column("body", JSON, nullable=False),
        Column("action", JSON, nullable=False),
        Column("approved_by", String(80), nullable=True),
        Column("approval_reason", Text, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        CheckConstraint(
            "status IN ('proposed','pending_approval','approved','executing','executed','rejected','execution_failed')"
        ),
    )
    Table(
        "executions", metadata,
        Column("id", String(36), primary_key=True),
        Column("recommendation_id", String(36), ForeignKey("recommendations.id"), nullable=False, unique=True),
        Column("idempotency_key", String(100), nullable=False, unique=True),
        Column("status", String(30), nullable=False),
        Column("simulation", Boolean, nullable=False),
        Column("result", JSON, nullable=True),
        Column("error", JSON, nullable=True),
        Column("actor", String(80), nullable=False),
        Column("request_id", String(64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "audit_logs", metadata,
        Column("id", String(36), primary_key=True),
        Column("actor", String(80), nullable=False),
        Column("action", String(80), nullable=False),
        Column("object_type", String(30), nullable=False),
        Column("object_id", String(100), nullable=False),
        Column("request_id", String(64), nullable=False, index=True),
        Column("outcome", String(30), nullable=False),
        Column("details", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Index("ix_audit_object_time", "object_id", "created_at"),
    )
    return metadata


def seed_legacy_rows(engine) -> None:
    """Insert rows through the old schema only (no new columns exist yet)."""
    metadata = legacy_metadata()
    tables = metadata.tables
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(tables["schema_versions"].insert().values(
            version=1, description="ProfitOps initial schema",
        ))
        connection.execute(tables["seed_versions"].insert().values(
            version="legacy-seed", created_at=now,
        ))
        connection.execute(tables["stores"].insert().values(
            id="legacy-store", name="Legacy Store", marketplace="amazon", currency="USD",
        ))
        connection.execute(tables["products"].insert().values(
            id="legacy-product", sku="SKU-LEGACY", name="Legacy Product",
        ))
        connection.execute(tables["orders"].insert().values(
            id="legacy-order", store_id="legacy-store", product_id="legacy-product",
            date=now.date(), quantity=2, sales=10_000, discounts=500, refunds=0,
            cogs=4_000, fulfillment=1_000, platform_fees=500, other_costs=250,
        ))
        connection.execute(tables["daily_operations"].insert().values(
            id="legacy-operation", store_id="legacy-store", product_id="legacy-product",
            date=now.date(), ad_spend=1_500, inventory=20, inbound_inventory=5, complete=True,
        ))
        connection.execute(tables["knowledge_documents"].insert().values(
            id="legacy-document", title="Legacy SOP", source="legacy", version="v1",
            effective_date=now.date(), content="legacy content",
            content_sha256="a" * 64, metadata_json={}, active=True,
            created_at=now, updated_at=now,
        ))
        connection.execute(tables["knowledge_chunks"].insert().values(
            id="legacy-chunk", document_id="legacy-document", chunk_index=0,
            heading="Legacy", content="legacy content", char_count=14,
            embedding=[0.1, 0.2], embedding_model="legacy-model", created_at=now,
        ))
        connection.execute(tables["analyses"].insert().values(
            id="legacy-analysis", idempotency_key="legacy-key", request={"store_id": "legacy-store"},
            actor="demo-analyst", request_id="legacy-request", status="completed",
            steps=[{"name": "receive_request"}], snapshot={"status": "completed"},
            result={"engine": "deterministic-rules-v1"},
            recommendation_id="legacy-recommendation", created_at=now, updated_at=now,
        ))
        connection.execute(tables["recommendations"].insert().values(
            id="legacy-recommendation", analysis_id="legacy-analysis", status="executed",
            title="Legacy recommendation", body={"problem": "legacy"},
            action={"type": "adjust_ad_budget"}, approved_by="demo-approver",
            approval_reason="legacy approval", created_at=now, updated_at=now,
        ))
        connection.execute(tables["executions"].insert().values(
            id="legacy-execution", recommendation_id="legacy-recommendation",
            idempotency_key="legacy-exec-key", status="executed", simulation=True,
            result={"simulation": True}, actor="demo-approver",
            request_id="legacy-request", created_at=now,
        ))
        connection.execute(tables["audit_logs"].insert().values(
            id="legacy-audit", actor="demo-approver", action="execution.executed",
            object_type="recommendation", object_id="legacy-recommendation",
            request_id="legacy-request", outcome="success",
            details={"execution_id": "legacy-execution"}, created_at=now,
        ))

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def build_engine(url: str):
    parsed = make_url(url)
    kwargs = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def begin_write(session: Session, engine) -> None:
    """Take SQLite's write lock before a read-then-insert transaction.

    SQLite WAL lets a deferred transaction read, then fail with
    ``SQLITE_BUSY_SNAPSHOT`` when another connection wrote in between.
    ``BEGIN IMMEDIATE`` serializes writers up front; PostgreSQL relies on the
    partial unique index plus savepoint recovery instead.
    """
    if engine.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _index_exists(bind, table: str, name: str) -> bool:
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return name in {item["name"] for item in inspector.get_indexes(table)}


LEGACY_KNOWLEDGE_ACTIVE_SHA_INDEX = "uq_knowledge_documents_active_sha"
TENANT_TABLES = (
    "stores", "products", "orders", "daily_operations", "knowledge_documents",
    "analyses", "recommendations", "executions", "audit_logs",
)


def _migrate_tenant_columns(connection) -> None:
    """Add tenant_id to existing tenant-owned tables and their indexes."""
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table_name in TENANT_TABLES:
        if table_name not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "tenant_id" not in columns:
            connection.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN tenant_id VARCHAR(40) NOT NULL DEFAULT 'demo'"
            ))
    for table_name in TENANT_TABLES:
        table = Base.metadata.tables.get(table_name)
        if table is None or table_name not in existing_tables:
            continue
        for index in table.indexes:
            # Unique tenant indexes (knowledge content hash) need dedupe first;
            # _migrate_knowledge_active_sha_index owns that ordering.
            if index.unique:
                continue
            if "tenant_id" in {column.name for column in index.columns}:
                index.create(bind=connection, checkfirst=True)


def _dedupe_active_documents(connection, document_model) -> None:
    from .models import utcnow

    # A Session bound to the caller's Connection joins its transaction, so the
    # dedupe and the index DDL commit or roll back together.
    with Session(bind=connection) as session:
        rows = session.execute(
            select(document_model.id, document_model.tenant_id, document_model.content_sha256)
            .where(document_model.active.is_(True))
            .order_by(
                document_model.tenant_id,
                document_model.content_sha256,
                document_model.created_at.asc().nulls_first(),
                document_model.id,
            )
        ).all()
        seen: set[tuple[str, str]] = set()
        for document_id, tenant_id, sha in rows:
            key = (tenant_id, sha)
            if key in seen:
                session.execute(
                    update(document_model)
                    .where(document_model.id == document_id)
                    .values(active=False, updated_at=utcnow())
                )
            else:
                seen.add(key)
        session.flush()


def _migrate_knowledge_active_sha_index(connection) -> None:
    """Deduplicate active documents, then enforce one active row per tenant+hash.

    Older databases may contain several active documents with the same content
    (created before the unique index existed). Keep the earliest row and
    soft-delete the rest: rows and their chunks are preserved, never deleted.
    The legacy index did not include tenant_id; it is dropped and replaced so
    two tenants can each own the same SOP content without sharing a row.
    A concurrent writer can insert a new duplicate between the dedupe and index
    creation, so the pair is retried a bounded number of times. Each attempt
    runs in a savepoint so a failed CREATE INDEX does not poison the caller's
    transaction (the Alembic migration calls this function too).
    """
    from .models import KNOWLEDGE_ACTIVE_SHA_INDEX, KnowledgeDocument

    connection.execute(text(f"DROP INDEX IF EXISTS {LEGACY_KNOWLEDGE_ACTIVE_SHA_INDEX}"))
    if _index_exists(connection, "knowledge_documents", KNOWLEDGE_ACTIVE_SHA_INDEX):
        return
    index = next(
        item for item in KnowledgeDocument.__table__.indexes
        if item.name == KNOWLEDGE_ACTIVE_SHA_INDEX
    )
    for attempt in range(3):
        _dedupe_active_documents(connection, KnowledgeDocument)
        try:
            with connection.begin_nested():
                index.create(bind=connection, checkfirst=True)
            return
        except (IntegrityError, OperationalError, ProgrammingError):
            # Either another process created the index first, or a new
            # duplicate landed between dedupe and DDL. Retry from dedupe.
            if _index_exists(connection, "knowledge_documents", KNOWLEDGE_ACTIVE_SHA_INDEX):
                return
            if attempt == 2:
                raise


def _migrate_analysis_job_columns(connection) -> None:
    """Add durable-job columns to databases created before the worker existed.

    ``create_all`` only creates missing tables; it never alters an existing
    ``analyses`` table. The additions are nullable or have a constant default,
    so they are safe on both SQLite and PostgreSQL. Alembic becomes the
    long-term migration path, but this keeps an existing demo database usable
    without a manual reset.
    """
    from .models import Analysis

    inspector = inspect(connection)
    if "analyses" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("analyses")}
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if connection.dialect.name == "postgresql" else "DATETIME"
    additions = {
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "max_attempts": "INTEGER NOT NULL DEFAULT 3",
        "next_run_at": timestamp_type,
        "lease_until": timestamp_type,
        "worker_id": "VARCHAR(80)",
    }
    for name, ddl in additions.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE analyses ADD COLUMN {name} {ddl}"))
    index = next(
        item for item in Analysis.__table__.indexes
        if item.name == "ix_analyses_status_next_run"
    )
    index.create(bind=connection, checkfirst=True)


def upgrade_legacy_schema(connection) -> None:
    """Bring a pre-tenancy database up to the baseline model in place.

    ``Base.metadata.create_all`` only creates missing tables, so existing
    business rows are never dropped or recreated. Column additions use
    constant defaults (``demo`` / ``0`` / ``3``), which SQLite and PostgreSQL
    both accept on populated tables.

    Revision 0002 calls this before changing the idempotency constraints. A
    genuine pre-tenancy database can only be stamped as ``0001_baseline``
    (stamping runs no DDL), so the revision itself must supply the missing
    tenancy and durable-job schema instead of relying on application startup.
    """
    Base.metadata.create_all(bind=connection)
    _migrate_tenant_columns(connection)
    _migrate_analysis_job_columns(connection)
    _migrate_knowledge_active_sha_index(connection)


def initialize_database(engine):
    from .models import SchemaVersion

    with engine.begin() as connection:
        upgrade_legacy_schema(connection)
    factory = make_session_factory(engine)
    with factory.begin() as session:
        if session.get(SchemaVersion, 1) is None:
            session.add(SchemaVersion(version=1, description="ProfitOps initial schema"))

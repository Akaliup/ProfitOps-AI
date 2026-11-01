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


def _index_exists(engine, table: str, name: str) -> bool:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return False
    return name in {item["name"] for item in inspector.get_indexes(table)}


def _dedupe_active_documents(engine, document_model) -> None:
    from .models import utcnow

    factory = make_session_factory(engine)
    with factory.begin() as session:
        rows = session.execute(
            select(document_model.id, document_model.content_sha256)
            .where(document_model.active.is_(True))
            .order_by(
                document_model.content_sha256,
                document_model.created_at.asc().nulls_first(),
                document_model.id,
            )
        ).all()
        seen: set[str] = set()
        for document_id, sha in rows:
            if sha in seen:
                session.execute(
                    update(document_model)
                    .where(document_model.id == document_id)
                    .values(active=False, updated_at=utcnow())
                )
            else:
                seen.add(sha)


def _migrate_knowledge_active_sha_index(engine) -> None:
    """Deduplicate active documents, then enforce one active row per SHA-256.

    Older databases may contain several active documents with the same content
    (created before the unique index existed). Keep the earliest row and
    soft-delete the rest: rows and their chunks are preserved, never deleted.
    A concurrent writer can insert a new duplicate between the dedupe and index
    creation, so the pair is retried a bounded number of times.
    """
    from .models import KNOWLEDGE_ACTIVE_SHA_INDEX, KnowledgeDocument

    if _index_exists(engine, "knowledge_documents", KNOWLEDGE_ACTIVE_SHA_INDEX):
        return
    index = next(
        item for item in KnowledgeDocument.__table__.indexes
        if item.name == KNOWLEDGE_ACTIVE_SHA_INDEX
    )
    for attempt in range(3):
        _dedupe_active_documents(engine, KnowledgeDocument)
        try:
            index.create(bind=engine, checkfirst=True)
            return
        except (IntegrityError, OperationalError, ProgrammingError):
            # Either another process created the index first, or a new
            # duplicate landed between dedupe and DDL. Retry from dedupe.
            if _index_exists(engine, "knowledge_documents", KNOWLEDGE_ACTIVE_SHA_INDEX):
                return
            if attempt == 2:
                raise


def initialize_database(engine):
    from .models import SchemaVersion

    Base.metadata.create_all(engine)
    _migrate_knowledge_active_sha_index(engine)
    factory = make_session_factory(engine)
    with factory.begin() as session:
        if session.get(SchemaVersion, 1) is None:
            session.add(SchemaVersion(version=1, description="ProfitOps initial schema"))

"""Re-embed every active knowledge document with the configured provider.

Retrieval filters chunks by ``embedding_model``, so switching
``KNOWLEDGE_EMBEDDING_PROVIDER`` / ``KNOWLEDGE_EMBEDDING_MODEL`` makes the existing
knowledge base invisible until the stored vectors are regenerated. This script
does that in place: documents, chunk ids, headings and audit history are kept, only
the vectors and ``embedding_model`` change.

Usage (reads the same .env as the app):
  .venv/Scripts/python.exe scripts/reindex_knowledge.py --dry-run
  .venv/Scripts/python.exe scripts/reindex_knowledge.py

It refuses to run when the configured provider is the offline hashing baseline and
``--allow-hashing`` is not passed, because that would silently downgrade a real
embedding index.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import build_engine, make_session_factory  # noqa: E402
from app.knowledge import build_embedding_provider, list_documents, reindex_document  # noqa: E402
from app.models import KnowledgeChunk  # noqa: E402
from app.security import Actor  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Re-embed knowledge chunks for the current provider")
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--dry-run", action="store_true", help="only report what would change")
    parser.add_argument("--allow-hashing", action="store_true", help="allow reindexing to hashing-v1")
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.knowledge_embedding_provider == "hashing" and not args.allow_hashing:
        print(
            "refusing: provider is hashing (configure KNOWLEDGE_EMBEDDING_PROVIDER=openai "
            "and KNOWLEDGE_EMBEDDING_MODEL, or pass --allow-hashing)",
        )
        return 2

    embeddings = build_embedding_provider(settings)
    engine = build_engine(settings.database_url)
    factory = make_session_factory(engine)
    actor = Actor(id="reindex-script", role="approver", tenant_id=args.tenant)
    try:
        with factory() as session:
            documents = list_documents(session, args.tenant)
            stale = {
                model: count
                for model, count in session.execute(
                    select(KnowledgeChunk.embedding_model, func.count(KnowledgeChunk.id))
                    .group_by(KnowledgeChunk.embedding_model)
                ).all()
            }
        print(f"provider={settings.knowledge_embedding_provider} model={embeddings.model}")
        print(f"database={settings.database_url}")
        print(f"documents={len(documents)} chunks_by_model={stale}")
        if args.dry_run:
            print("dry-run: nothing written")
            return 0

        changed = skipped = 0
        for document in documents:
            with factory.begin() as session:
                result = reindex_document(
                    session, document.id, embeddings,
                    actor=actor, request_id=f"reindex-{document.id}", tenant_id=args.tenant,
                )
            if result.reindexed:
                changed += 1
                print(f"  reindexed {result.title} ({result.chunk_count} chunks)")
            else:
                skipped += 1
        with factory() as session:
            after = {
                model: count
                for model, count in session.execute(
                    select(KnowledgeChunk.embedding_model, func.count(KnowledgeChunk.id))
                    .group_by(KnowledgeChunk.embedding_model)
                ).all()
            }
        print(f"reindexed={changed} already_current={skipped} chunks_by_model={after}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

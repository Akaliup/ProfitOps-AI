"""One-shot initialization for a fresh deployment.

The cloud Compose stack runs Alembic first, then this module, and only starts
the API and worker after it succeeds. Keeping seeding in a separate process
avoids two application processes racing to create the schema or claim the
seed version on a brand-new database.
"""
from __future__ import annotations

from .config import get_settings
from .db import build_engine, initialize_database, make_session_factory
from .knowledge import build_embedding_provider, seed_knowledge
from .seed import seed_demo, seed_identity


def main() -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        initialize_database(engine)
        factory = make_session_factory(engine)
        if not settings.demo_mode:
            print("demo_mode=false: schema initialized; demo data not seeded", flush=True)
            return 0
        print(seed_identity(factory, settings), flush=True)
        if not settings.auto_seed:
            print("auto_seed=false: demo identity seeded; demo data not seeded", flush=True)
            return 0
        print(seed_demo(factory), flush=True)
        embeddings = build_embedding_provider(settings)
        print(seed_knowledge(factory, embeddings, settings, tenant_id="demo"), flush=True)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

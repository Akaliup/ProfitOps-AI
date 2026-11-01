"""Baseline schema for a fresh ProfitOps AI database.

Existing databases created by ``initialize_database`` already contain these
tables. Stamp them instead of running this revision:

    alembic -c backend/alembic.ini stamp 0001_baseline

Future schema changes must be explicit ``op.*`` revisions; this baseline only
materializes the current SQLAlchemy metadata.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app import models  # noqa: F401  (populates Base.metadata)
from app.db import Base

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())

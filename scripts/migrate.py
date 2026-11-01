"""Run Alembic migrations with the project's database settings.

Examples (from the repository root):
    python scripts/migrate.py upgrade head
    python scripts/migrate.py current
    python scripts/migrate.py stamp 0001_baseline   # existing pre-Alembic DB
    python scripts/migrate.py downgrade base
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ProfitOps AI database migrations")
    parser.add_argument("action", choices=["upgrade", "downgrade", "stamp", "current", "history"])
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args()

    config = Config(str(BACKEND / "alembic.ini"))
    if args.action == "upgrade":
        command.upgrade(config, args.revision)
    elif args.action == "downgrade":
        command.downgrade(config, args.revision)
    elif args.action == "stamp":
        command.stamp(config, args.revision)
    elif args.action == "current":
        command.current(config)
    else:
        command.history(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

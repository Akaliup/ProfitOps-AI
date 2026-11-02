"""Seed E2E-only fixtures into a disposable SQLite database.

The browser suite needs states the normal API cannot produce on demand: a
second tenant, and a failed analysis whose retry can be observed in the UI.
This script writes them directly into the E2E database after the backend has
initialized it. It is idempotent for the tenant fixtures, creates a fresh
failed analysis per run, and refuses to touch the runtime database.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models import Analysis, Tenant, User  # noqa: E402
from app.security import hash_password  # noqa: E402

TENANT_B = "tenant-b"
TENANT_B_EMAIL = "analyst@tenant-b.local"
TENANT_B_PASSWORD = "tenant-b-password"
TENANT_B_ANALYSIS_ID = "tenant-b-analysis"
# Analyses store the resolved scope (MetricsInput), not the raw HTTP body.
PERIOD = {
    "period": {"start_date": "2026-10-01", "end_date": "2026-10-30"},
    "store_id": None,
}


def seed(database: Path, output: Path) -> dict:
    now = datetime.now(timezone.utc)
    failed_id = f"e2e-failed-{uuid4().hex[:12]}"
    engine = create_engine(f"sqlite:///{database}")
    try:
        # Playwright runs one beforeAll per worker, so two workers can seed the
        # same disposable database at once. The loser of the tenant insert
        # retries and then sees the committed rows from the winner.
        for attempt in range(2):
            try:
                with Session(engine) as session:
                    if session.get(Tenant, TENANT_B) is None:
                        session.add(Tenant(id=TENANT_B, name="E2E 隔离租户"))
                    user = session.scalar(select(User).where(
                        User.tenant_id == TENANT_B, User.email == TENANT_B_EMAIL,
                    ))
                    if user is None:
                        session.add(User(
                            tenant_id=TENANT_B, email=TENANT_B_EMAIL, display_name="租户 B 分析师",
                            role="analyst", password_hash=hash_password(TENANT_B_PASSWORD),
                        ))
                    if session.get(Analysis, TENANT_B_ANALYSIS_ID) is None:
                        session.add(Analysis(
                            id=TENANT_B_ANALYSIS_ID, tenant_id=TENANT_B,
                            idempotency_key="tenant-b-failed", request=PERIOD,
                            actor="tenant-b-analyst", request_id="tenant-b-request",
                            status="failed", steps=[], snapshot={},
                            error={"code": "insufficient_data", "message": "租户 B 无经营数据"},
                            attempts=1, max_attempts=3, created_at=now, updated_at=now,
                        ))
                    session.add(Analysis(
                        id=failed_id, tenant_id="demo", idempotency_key=f"e2e-failed-{failed_id}",
                        request=PERIOD, actor="demo-analyst", request_id=f"e2e-request-{failed_id}",
                        status="failed",
                        steps=[
                            {
                                "name": "receive_request", "label": "接收请求", "status": "completed",
                                "started_at": now.isoformat(), "request_id": f"e2e-request-{failed_id}",
                                "phase": "analysis", "duration_ms": 1.2, "summary": "请求已接收",
                            },
                            {
                                "name": "llm_root_cause_analysis", "label": "DeepSeek 根因分析",
                                "status": "failed", "started_at": now.isoformat(),
                                "request_id": f"e2e-request-{failed_id}", "phase": "analysis",
                                "duration_ms": 800.0, "summary": "工具执行失败",
                            },
                        ],
                        snapshot={},
                        error={"code": "llm_tool_execution_failed", "message": "工具执行失败：模拟账本不可用"},
                        attempts=3, max_attempts=3, created_at=now, updated_at=now,
                    ))
                    session.commit()
                break
            except IntegrityError:
                if attempt == 1:
                    raise
    finally:
        engine.dispose()
    payload = {
        "failed_analysis_id": failed_id,
        "tenant_b_analysis_id": TENANT_B_ANALYSIS_ID,
        "tenant_b_email": TENANT_B_EMAIL,
        "tenant_b_password": TENANT_B_PASSWORD,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, help="SQLite file used only for E2E")
    parser.add_argument("--out", required=True, help="JSON file describing the fixtures")
    args = parser.parse_args()
    database = Path(args.database).resolve()
    if database.name == "profitops.db":
        raise SystemExit("refusing to seed the runtime database")
    if not database.exists():
        raise SystemExit(f"E2E database not initialized yet: {database}")
    payload = seed(database, Path(args.out).resolve())
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Review how migration handles duplicate active knowledge documents.

The database is built from the real pre-tenancy schema in a disposable
temporary directory. It deliberately contains several active documents with
identical content, which older databases could hold before the unique index
existed. The script stamps ``0001_baseline``, upgrades to ``head``, verifies
the documented handling, upgrades again to prove idempotence, and writes a
sanitized Markdown report.

It never opens ``data/profitops.db`` or any other runtime database.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from legacy_schema import (  # noqa: E402
    LEGACY_ACTIVE_SHA_INDEX,
    legacy_metadata,
    seed_legacy_rows,
)

SHARED_SHA = "a" * 64
OTHER_SHA = "b" * 64
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
BUSINESS_TABLES = (
    "stores", "products", "orders", "daily_operations", "knowledge_documents",
    "knowledge_chunks", "analyses", "recommendations", "executions", "audit_logs",
)


def _insert_duplicates(connection, tables) -> None:
    # Reproduce databases older than the legacy unique index.
    connection.execute(text(f"DROP INDEX IF EXISTS {LEGACY_ACTIVE_SHA_INDEX}"))
    documents = [
        # id, title, source, version, active, day offset
        ("duplicate-document-b", "Legacy SOP copy B", "legacy://sop/b", "v2", True, 1),
        ("duplicate-document-c", "Legacy SOP copy C", "legacy://sop/c", "v3", True, 2),
        ("duplicate-document-d", "Legacy SOP copy D", "legacy://sop/d", "v4", True, 3),
        # Inactive history with the same content must stay inactive.
        ("inactive-document", "Legacy SOP retired", "legacy://sop/retired", "v0", False, -1),
    ]
    for document_id, title, source, version, active, offset in documents:
        created = BASE_TIME + timedelta(days=offset)
        connection.execute(tables["knowledge_documents"].insert().values(
            id=document_id, title=title, source=source, version=version,
            effective_date=created.date(), content="legacy content",
            content_sha256=SHARED_SHA, metadata_json={}, active=active,
            created_at=created, updated_at=created,
        ))
        connection.execute(tables["knowledge_chunks"].insert().values(
            id=f"chunk-{document_id}", document_id=document_id, chunk_index=0,
            heading=title, content="legacy content", char_count=14,
            embedding=[0.1, 0.2], embedding_model="legacy-model", created_at=created,
        ))
    # Different content is not a duplicate and must stay active.
    connection.execute(tables["knowledge_documents"].insert().values(
        id="other-document", title="Different SOP", source="legacy://sop/other",
        version="v1", effective_date=BASE_TIME.date(), content="different content",
        content_sha256=OTHER_SHA, metadata_json={}, active=True,
        created_at=BASE_TIME, updated_at=BASE_TIME,
    ))


def _documents(engine) -> list[tuple]:
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT id, source, version, active, created_at, updated_at, content_sha256 "
            "FROM knowledge_documents ORDER BY id"
        )).all()


def _chunks(engine) -> list[tuple]:
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT id, document_id, chunk_index FROM knowledge_chunks ORDER BY id"
        )).all()


def _counts(engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in BUSINESS_TABLES
        }


def _snapshot(engine) -> dict:
    return {
        "documents": _documents(engine),
        "chunks": _chunks(engine),
        "counts": _counts(engine),
    }


def review(workdir: Path, report: Path) -> dict:
    database = workdir / "legacy-duplicates.db"
    engine = create_engine(f"sqlite:///{database}")
    metadata = legacy_metadata()
    metadata.create_all(engine)
    seed_legacy_rows(engine)
    with engine.begin() as connection:
        _insert_duplicates(connection, metadata.tables)
    before = _snapshot(engine)

    import os
    os.environ["PROFITOPS_DATABASE_URL"] = f"sqlite:///{database}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    command.stamp(config, "0001_baseline")
    command.upgrade(config, "head")
    after = _snapshot(engine)

    with engine.connect() as connection:
        fk_violations = connection.execute(text("PRAGMA foreign_key_check")).all()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        schema_rows = connection.execute(text("SELECT COUNT(*) FROM schema_versions")).scalar_one()

    before_docs = {row[0]: row for row in before["documents"]}
    after_docs = {row[0]: row for row in after["documents"]}
    checks = {
        "all_document_ids_preserved": set(before_docs) == set(after_docs),
        "all_chunks_preserved": before["chunks"] == after["chunks"],
        "foreign_keys_valid": not fk_violations,
        "one_active_document_per_content": sum(
            1 for row in after["documents"] if row[3] and row[6] == SHARED_SHA
        ) == 1,
        "earliest_duplicate_kept_active": bool(after_docs["legacy-document"][3]),
        "extra_active_duplicates_soft_deleted": all(
            not bool(after_docs[document_id][3])
            for document_id in ("duplicate-document-b", "duplicate-document-c", "duplicate-document-d")
        ),
        "inactive_history_unchanged": not bool(after_docs["inactive-document"][3]),
        "different_content_unchanged": bool(after_docs["other-document"][3]),
        "business_row_counts_unchanged": before["counts"] == after["counts"],
        "revision_is_head": revision == "0002_tenant_scoped_idempotency",
        "single_schema_version_row": schema_rows == 1,
    }

    command.upgrade(config, "head")
    repeated = _snapshot(engine)
    checks["repeated_upgrade_is_noop"] = repeated == after
    engine.dispose()

    if not all(checks.values()):
        failed = [name for name, ok in checks.items() if not ok]
        raise SystemExit(f"review failed: {', '.join(failed)}")

    rows = []
    for document_id, source, version, active, _created, _updated, sha in after["documents"]:
        before_active = before_docs[document_id][3]
        if document_id == "legacy-document":
            handling = "保留为唯一活跃文档（创建时间最早）"
        elif document_id.startswith("duplicate-document"):
            handling = "同一内容重复活跃 → 迁移置为 inactive，记录与分片保留"
        elif document_id == "inactive-document":
            handling = "迁移前已 inactive → 保持不变"
        else:
            handling = "内容不同 → 保持 active"
        rows.append({
            "document_id": document_id,
            "source": source,
            "version": version,
            "active_before": bool(before_active),
            "active_after": bool(active),
            "handling": handling,
            "shared_content": sha == SHARED_SHA,
        })

    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 历史库重复知识文档迁移审查（脱敏）",
        "",
        f"- 审查日期：{datetime.now().date().isoformat()}",
        "- 数据来源：按 `backend/tests/legacy_schema.py` 构造的隔离临时 SQLite 数据库；",
        "  未读取或修改 `data/profitops.db` 或任何真实业务数据库。",
        f"- 迁移路径：`stamp 0001_baseline` → `upgrade head`（{revision}）",
        "- 重复内容：5 条文档共享同一 SHA-256（内容为合成文本 `legacy content`），",
        "  其中 4 条在迁移前为 active，1 条为 inactive 历史记录。",
        "",
        "## 文档处理结果",
        "",
        "| 文档 ID | source | version | 迁移前 active | 迁移后 active | 处理结果 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['document_id']}` | `{row['source']}` | `{row['version']}` | "
            f"{row['active_before']} | {row['active_after']} | {row['handling']} |"
        )
    lines += [
        "",
        "## 完整性校验",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
    ]
    for name, ok in checks.items():
        lines.append(f"| `{name}` | {'PASS' if ok else 'FAIL'} |")
    lines += [
        "",
        "## 说明",
        "",
        "- 迁移保留全部文档行和全部分片；重复活跃文档只做软删除（`active=false`），",
        "  不删除记录、不改写 `document_id` 引用。",
        "- 唯一活跃文档的选择顺序为 `created_at ASC, id ASC`，因此创建最早的",
        "  `legacy-document` 保留为 active。",
        "- 再次执行 `upgrade head` 不产生任何行、时间戳或计数变化。",
        "- 本报告仅含合成 ID、来源和版本，不含真实业务数据或凭据。",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return {"checks": checks, "documents": rows, "report": str(report)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", help="Disposable directory for the synthetic database")
    parser.add_argument(
        "--report", default=str(ROOT / "docs" / "LEGACY_DUPLICATE_REVIEW.md"),
        help="Markdown report path",
    )
    args = parser.parse_args()
    workdir = Path(args.workdir).resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="profitops-legacy-review-"))
    result = review(workdir, Path(args.report).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

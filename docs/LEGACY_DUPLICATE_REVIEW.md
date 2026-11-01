# 历史库重复知识文档迁移审查（脱敏）

- 审查日期：2026-11-02
- 数据来源：按 `backend/tests/legacy_schema.py` 构造的隔离临时 SQLite 数据库；
  未读取或修改 `data/profitops.db` 或任何真实业务数据库。
- 迁移路径：`stamp 0001_baseline` → `upgrade head`（0002_tenant_scoped_idempotency）
- 重复内容：5 条文档共享同一 SHA-256（内容为合成文本 `legacy content`），
  其中 4 条在迁移前为 active，1 条为 inactive 历史记录。

## 文档处理结果

| 文档 ID | source | version | 迁移前 active | 迁移后 active | 处理结果 |
| --- | --- | --- | --- | --- | --- |
| `duplicate-document-b` | `legacy://sop/b` | `v2` | True | False | 同一内容重复活跃 → 迁移置为 inactive，记录与分片保留 |
| `duplicate-document-c` | `legacy://sop/c` | `v3` | True | False | 同一内容重复活跃 → 迁移置为 inactive，记录与分片保留 |
| `duplicate-document-d` | `legacy://sop/d` | `v4` | True | False | 同一内容重复活跃 → 迁移置为 inactive，记录与分片保留 |
| `inactive-document` | `legacy://sop/retired` | `v0` | False | False | 迁移前已 inactive → 保持不变 |
| `legacy-document` | `legacy` | `v1` | True | True | 保留为唯一活跃文档（创建时间最早） |
| `other-document` | `legacy://sop/other` | `v1` | True | True | 内容不同 → 保持 active |

## 完整性校验

| 检查项 | 结果 |
| --- | --- |
| `all_document_ids_preserved` | PASS |
| `all_chunks_preserved` | PASS |
| `foreign_keys_valid` | PASS |
| `one_active_document_per_content` | PASS |
| `earliest_duplicate_kept_active` | PASS |
| `extra_active_duplicates_soft_deleted` | PASS |
| `inactive_history_unchanged` | PASS |
| `different_content_unchanged` | PASS |
| `business_row_counts_unchanged` | PASS |
| `revision_is_head` | PASS |
| `single_schema_version_row` | PASS |
| `repeated_upgrade_is_noop` | PASS |

## 说明

- 迁移保留全部文档行和全部分片；重复活跃文档只做软删除（`active=false`），
  不删除记录、不改写 `document_id` 引用。
- 唯一活跃文档的选择顺序为 `created_at ASC, id ASC`，因此创建最早的
  `legacy-document` 保留为 active。
- 再次执行 `upgrade head` 不产生任何行、时间戳或计数变化。
- 本报告仅含合成 ID、来源和版本，不含真实业务数据或凭据。

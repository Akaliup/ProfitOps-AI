# 验证记录

本文件记录本次交付实际执行的命令与结果。未实际执行的验证不会标记为通过。

## 2026-11-02 PR #9 成本与调用口径收口（Claude Code）

分支 `feat/final-agent-validation`，PR #9，基线 `7da915e`。本轮只修复 native Function Calling 的逐轮成本完整性与模型调用/Provider 请求计数口径，不新增功能，不调用真实模型，不修改既有评测 JSON、固定金标或历史运行数据。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（Python 3.12.8，含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... .venv/Scripts/python.exe -m pytest backend/tests -q` | **266 passed, 0 failed, 0 skipped**（114.35s，1 个第三方 DeprecationWarning） |
| 后端全量（Python 3.11.16，含 PostgreSQL） | 同上，使用 `.venv311/Scripts/python.exe` | **266 passed, 0 failed, 0 skipped**（189.64s，1 个第三方 DeprecationWarning） |
| 运维汇总定向回归 | `.venv/Scripts/python.exe -m pytest backend/tests/test_ops.py -q` | **17 passed**（8.01s） |
| 前端单元/交互 | `npm --prefix frontend test -- --run` | **16 passed**（1 file，9.15s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | **PASS**；Vite 6.4.5，2252 modules，4.57s |
| Playwright 浏览器 E2E | `PROFITOPS_E2E_DATABASE_URL=sqlite:///... npm --prefix frontend run test:e2e` | **7 passed**（21.7s）；独立临时库，`LLM_PROVIDER=disabled`、`LLM_API_KEY=` |
| 真实 LLM | 未调用 | **NOT RUN**；本轮没有真实调用授权，付费 token 0 |
| GitHub Actions | 推送后由 `.github/workflows/ci.yml` 执行 | 推送后确认，不以本地结果代替 CI |

本轮定向回归覆盖：native 两轮均有 Usage 且可定价时 `cost.status=complete`，总额为两轮独立估算之和；第一轮有 Usage、第二轮缺失时 `partial`、`amount_usd=null`，`known_subtotal_usd` 只含第一轮；一轮成功、一轮失败且失败轮缺 Usage 时同样为 `partial`；native 两轮但 `attempts=3` 时 `model_calls=2`、`provider_requests=3`、`provider_retries=1`；非 native 单次逻辑调用 `attempts=3` 时 `model_calls=1`、`provider_requests=3`；请求发出前即失败（`attempts=0`、无 Usage、无轮次）不计入模型调用与成本分母。`verify_llm` 成功事件与随后终止事件的去重测试已改为两轮 native 记录，确保不会把同一次运行重复计数。

## 2026-11-02 PR #9 运维汇总 P2 修复复验（Claude Code）

分支 `feat/final-agent-validation`，PR #9，基线 `e99a016`。本轮只修复 `GET /api/ops/summary` 的成本口径、审计历史去重与查询边界，不新增功能，不调用真实模型，不修改既有评测 JSON、固定金标或历史失败数据。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（Python 3.12.8，含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... .venv/Scripts/python.exe -m pytest backend/tests -q` | **260 passed, 0 failed, 0 skipped**（105.95s，1 个第三方 DeprecationWarning） |
| 后端全量（Python 3.11.16，含 PostgreSQL） | 同上，使用 `.venv311/Scripts/python.exe` | **260 passed, 0 failed, 0 skipped**（175.21s，1 个第三方 DeprecationWarning） |
| 运维汇总定向回归 | `.venv/Scripts/python.exe -m pytest backend/tests/test_ops.py -q` | **11 passed**（5.53s） |
| SQLite 迁移 | `.venv/Scripts/python.exe -m pytest backend/tests/test_migrations.py -q` | **4 passed**（1.05s） |
| PostgreSQL 迁移与租户幂等 | `PROFITOPS_TEST_POSTGRES_URL=... .venv/Scripts/python.exe -m pytest backend/tests/test_postgres_migrations.py backend/tests/test_postgres_idempotency.py -q` | **3 passed**（3.40s），使用一次性 schema |
| 前端单元/交互 | `npm --prefix frontend test` | **16 passed**（1 file，8.90s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | **PASS**；Vite 6.4.5，2252 modules，4.83s |
| Playwright 浏览器 E2E | `PROFITOPS_E2E_DATABASE_URL=sqlite:///... npm --prefix frontend run test:e2e` | **7 passed**（22.5s）；独立临时库，`LLM_PROVIDER=disabled`、`LLM_API_KEY=` |
| HTTP 冒烟 | 独立临时 SQLite + 8015 端口 + `LLM_PROVIDER=disabled`：`python scripts/smoke.py --base-url http://127.0.0.1:8015` | **15/15 passed**，`failed=0` |
| 空白字符 | `git diff --check` | 通过 |
| 真实 LLM | 未调用 | **NOT RUN**；本轮没有真实调用授权，付费 token 0 |
| GitHub Actions | 推送后由 `.github/workflows/ci.yml` 执行 | 推送后确认，不以本地结果代替 CI |

本轮定向回归覆盖：无异常/数据不足/未配置模型不进入成本分母；一次可定价调用加一次未知价格调用时 `amount_usd=null` 且 `known_subtotal_usd` 单独展示；同一 Analysis 前两次失败、第三次成功时历史调用、Token 与成本不覆盖也不重复；成功调用缺少 Usage 时标记 `partial`；租户 A/B 的 LLM 审计不串读；250 条 Analysis 下统计正确且最近失败明细限制为 10 条。汇总改为 SQL 聚合与分批流式读取，窗口内审计事件按 `created_at <= end` 截断，旧数据仅在窗口内没有审计事件时回退读取 `Analysis.result.llm`。

## 2026-11-02 最终功能收尾验收（feat/final-agent-validation）

基线：`origin/master` 合并 PR #8 后的 `3876cc6`。本轮在独立分支执行；后端使用 `.venv`（Python 3.12.8）与 `.venv311`（Python 3.11.16，`backend/requirements.lock.txt`），PostgreSQL 使用本机 `profitops-pg16-test`（PostgreSQL 16，端口 55432，测试库 `profitops_test`）。除真实 DeepSeek 评测外，不读写 `data/profitops.db`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（Python 3.12，含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... .venv/Scripts/python.exe -m pytest backend/tests -q` | **253 passed, 0 failed, 0 skipped**（94.59s，1 个第三方 DeprecationWarning） |
| 后端全量（Python 3.11，含 PostgreSQL） | 同上，使用 `.venv311/Scripts/python.exe` | **253 passed, 0 failed, 0 skipped**（161.11s，1 个第三方 DeprecationWarning） |
| SQLite / PostgreSQL 迁移 | `pytest backend/tests/test_migrations.py backend/tests/test_postgres_migrations.py -q` | **6 passed**（SQLite 4 + PostgreSQL 2，3.22s） |
| Worker 重试 / fencing / 死信 | `pytest backend/tests/test_worker.py -q` | **22 passed**（12.35s） |
| JWT / RBAC / 多租户 | `pytest backend/tests/test_auth_tenant.py -q` | **13 passed**（10.27s） |
| 可观测性 / 限流 / 运维汇总 | `pytest backend/tests/test_observability.py backend/tests/test_ops.py -q` | **24 passed**（8.02s） |
| 原生离线回归 | `PYTHONPATH=backend .venv/Scripts/python.exe backend/evals/runner.py native` | **PASS**：37 runs；正常 3/3、负向对照 34/34；task completion 1.0；safety 1.0；网络调用 0，付费 token 0 |
| 真实 DeepSeek 评测 | `runner.py native --real --authorize-real-calls ... --max-calls 6`（分 3 段执行） | **执行完成**：12/12 次预算内调用；工具选择 6/6、参数 12/12、证据 2/2；任务完成 1/3；0 副作用；估算 US$0.009825。详见 `docs/AGENT_REAL_EVALUATION.md` |
| BGE-M3 真实检索对比 | `PYTHONPATH=backend .venv/Scripts/python.exe backend/evals/rag_real.py --provider both --json docs/rag-real-embedding-2026-11-02.json` | **BLOCKED**：Ollama 未安装 `bge-m3`，真实 HTTP 404；hashing 基线 COMPLETED（42 查询，hit@1 97.22%、hit@3 100%）。详见 `docs/RAG_REAL_EMBEDDING_REPORT.md` |
| 前端单元测试 | `npm --prefix frontend test` | **16 passed**（1 file，8.38s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | **PASS**；2252 modules，5.12s |
| Playwright 浏览器 E2E | `PROFITOPS_E2E_DATABASE_URL=sqlite:///... npm --prefix frontend run test:e2e` | **7 passed**（17.5s）；独立临时库，`LLM_PROVIDER=disabled`、`LLM_API_KEY=`；含 Agent 运维页角色限制、真实聚合、Trace 跳转 |
| HTTP 冒烟 | 独立临时 SQLite + 8010 端口 + `LLM_PROVIDER=disabled`：`python scripts/smoke.py` | **15/15 passed**，`failed=0` |
| 后端 Docker 镜像 | `docker build -f backend/Dockerfile -t profitops-backend:acceptance .` | **PASS**；首次因 pip 索引响应截断失败，重试成功 |
| 前端 Docker 镜像 | `docker build -f frontend/Dockerfile -t profitops-frontend:acceptance .` | **PASS** |
| GitHub Actions | 推送后由 `.github/workflows/ci.yml` 执行 | **NOT RUN（本轮尚未推送）**；不把本地结果写成 CI 结果 |

本轮 E2E 暴露并修复了两个测试基础设施问题：Playwright 同一 spec 的多个 worker 会并发执行 `beforeAll`，导致 fixture 租户唯一键竞争；共享数据库下测试依赖“最新分析”顺序。修复为 `fullyParallel: false`（同一 spec 内串行、不同 spec 仍并行）、fixture 插入竞争重试，以及按 fixture ID 显式选择失败分析。没有放宽生产逻辑或断言。

## 2026-11-02 最终验收收口（Claude Code）

分支 `feat/platform-reliability`，基线 `dd104c2`，PR #8。本轮只补齐验收证据：浏览器 E2E、Python 3.11、历史库重复文档、真实 LLM 状态；不新增业务功能，不调用真实 LLM，不修改 `data/profitops.db`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（Python 3.12.8，含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... .venv/Scripts/python.exe -m pytest backend/tests -q` | 240 passed, 0 failed, 0 skipped（121.10s，1 个第三方 DeprecationWarning） |
| 后端全量（Python 3.11.16，含 PostgreSQL） | 独立 3.11 venv + `backend/requirements.lock.txt` + `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests -q` | 240 passed, 0 failed, 0 skipped（193.75s，1 个第三方 DeprecationWarning） |
| 迁移测试（Python 3.11） | 同一 3.11 venv：`pytest backend/tests/test_migrations.py backend/tests/test_postgres_migrations.py -q` | 6 passed（SQLite 4 + PostgreSQL 2，3.53s） |
| 前端单元测试 | `npm --prefix frontend test` | 15 passed（1 file，8.27s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules，4.55s |
| 浏览器 E2E（隔离临时库） | `PROFITOPS_E2E_DATABASE_URL=sqlite:///... npm --prefix frontend run test:e2e -- --workers=1` | 6 passed（30.0s）；后端 `LLM_PROVIDER=disabled`、`LLM_API_KEY=` |
| HTTP 冒烟 | 独立临时 SQLite + 端口 8015 + `LLM_PROVIDER=disabled`：`python scripts/smoke.py --base-url http://127.0.0.1:8015` | 15/15 通过，failed=0 |
| 历史库重复文档 | `python scripts/legacy_duplicate_review.py` | 12/12 检查 PASS；详见 `docs/LEGACY_DUPLICATE_REVIEW.md` |
| Python 3.11 CI | `.github/workflows/ci.yml` 新增 `Backend pytest (Python 3.11)` | 与现有 3.12 job 相同的锁定依赖、pytest、回放评测和 Alembic 检查；推送后由 GitHub Actions 执行 |
| 真实 LLM | 只读取 `Settings().llm_configured` | `configured=true`（provider=`deepseek`）；未输出密钥；本轮无新增授权，未发起真实调用 |
| 空白字符 | `git diff --check` | 通过 |
| 敏感文件 | 暂存区与未跟踪文件检查 | 未包含 `.env`、API Key、数据库、缓存或构建产物 |

E2E 覆盖（6/6）：

1. `JWT 登录、401、角色权限与跨租户隔离（真实 API）`：登录与 `/api/auth/me`、匿名 401、篡改 JWT 401、错误密码 401、analyst 创建知识文档 403、租户 B 登录、跨租户读取 404、本租户读取 200、列表与推荐隔离。
2. `失败状态展示与手动重试（真实 Worker）`：页面显示 `llm_tool_execution_failed`、`尝试 3/3` 和重试按钮；点击后重新排队并最终进入 `waiting_approval`，生成 `recommendation_id`，无页面异常。
3. `知识库页面：新增、检索与停用（真实 API）`：approver 新增文档、检索片段、停用后列表移除。
4. `真实 API：分析、越权拦截、审批、模拟执行、幂等与审计`。
5. `Agent Trace：原生轮次、失败调用与移动端布局可见`。
6. `拒绝分支和手机视图保持可用`。

**NOT TESTED（未测试）**：

- 前端登录页：本分支未实现登录表单，前端通过 `/api/demo` 选择演示身份；浏览器内的用户名/密码登录流程未测试。
- 登出：后端没有 logout 端点，前端没有登出入口，未测试。
- 独立运营/ops 页面：本分支不存在该页面，未测试。
- 浏览器内 401 全局跳转/拦截：无登录页可跳转，未测试；401 与角色校验仅在 E2E 用例 1 的真实 API 层验证。
- 非 Chromium 浏览器、生产容器内的 E2E：未测试；本轮 E2E 使用 Vite dev server + Chromium。

E2E 的 Playwright 配置在设置 `PROFITOPS_E2E_DATABASE_URL` 时启动独立后端，并将后端 `reuseExistingServer` 固定为 `false`，避免误复用绑定到 `data/profitops.db` 的已运行进程。E2E 会重写 `docs/screenshots/*.png`；这些副作用文件未纳入本次提交。

Python 3.11 说明：`py -0p` 仅列出 3.14 与 3.12，项目 `.venv` 为 3.12.8，本机原先没有 3.11。本轮用 `uv python install 3.11` 获取 CPython 3.11.16，在操作系统临时目录创建独立 venv，安装 `backend/requirements.lock.txt` 后执行全量测试；未覆盖、未修改现有 `.venv`。CI 采用新增独立 job 而非矩阵，以保留现有必需检查名 `Backend pytest`，同时用官方 `actions/setup-python` 固定 3.11。

真实 LLM：`LLM_API_KEY` 已配置（`configured=true`），但 `docs/EVALUATION.md` D5 要求显式授权字符串与正整数 `--max-calls`；本轮没有新的真实调用授权，因此 `REAL_LLM_EVAL=NOT RUN`，未执行 `native --real`，也没有用 FakeProvider 冒充真实 DeepSeek。历史 Phase 2B 的 22 次真实调用不在本轮范围。

历史库重复文档：使用 `backend/tests/legacy_schema.py` 构造隔离临时 SQLite（6 条文档，5 条共享同一 SHA-256，其中 4 条迁移前 active），执行 `stamp 0001_baseline → upgrade head`。全部文档 ID 与分片保留、外键有效、每组内容仅 1 条 active、最早创建的 `legacy-document` 保留、3 条重复活跃文档软删除、原有 inactive 与不同内容文档不变、业务表行数不变、重复 upgrade no-op；12/12 PASS。未读取或修改 `data/profitops.db`。

## 2026-11-01 PR #8 最终合并门禁（Claude Code）

分支 `feat/platform-reliability`，基线 `fb02522`。本轮只处理三项明确问题：真实历史库迁移（P1）、指标标签基数（P2）、租约到期严格保护（P3）；不扩展功能，不调用付费模型，不修改 `data/profitops.db`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests -q` | 240 passed, 0 failed, 0 skipped（120.71s，1 个第三方 DeprecationWarning） |
| 历史 SQLite 迁移 | `python -m pytest backend/tests/test_migrations.py -q` | 4 passed；真实前租户 schema（无 `tenants`/`users`、`tenant_id`、任务字段）+ `stamp 0001_baseline → upgrade head`；行数与外键保留；列宽 200、复合唯一、租约字段、索引到位；重复 upgrade no-op；跨租户同键可用、同租户重复键拒绝；重复活跃文档保留最新一行并软删除其余；含跨租户同键时 downgrade 因唯一约束失败且保留原行 |
| 历史 PostgreSQL 迁移 | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests/test_postgres_migrations.py -q` | 2 passed；一次性 schema + 真实前租户 schema，原地升级、数据与外键保留、约束与索引正确、重复 upgrade no-op；downgrade 冲突时失败且保留行；schema 结束后删除 |
| 指标标签基数 | `python -m pytest backend/tests/test_observability.py -q` | 16 passed；40 个随机未匹配路径只产生一个 `<unmatched>` 404 序列；匹配路由使用模板 `/api/analyses/{analysis_id}`；25 个随机路径的提前 429 只产生一个 `<rate_limited>` 序列 |
| 租约到期保护 | `python -m pytest backend/tests/test_worker.py -q` | 22 passed；恰好到期、到期未接管（`write_job` 与 `create_recommendation`）、`assert_owned`、心跳延迟、已被接管、活跃租约正常续租 |
| 前端测试 | `npm --prefix frontend test -- --run` | 15 passed（1 个测试文件，7.82s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules，4.57s |
| HTTP 冒烟 | 独立临时 SQLite + 端口 8015 + `LLM_PROVIDER=disabled`：`python scripts/smoke.py --base-url http://127.0.0.1:8015` | 15/15 通过，failed=0 |
| Alembic 新库与降级 | 临时 SQLite：`python scripts/migrate.py upgrade head` / `current` / `python -m alembic -c backend/alembic.ini check` / `downgrade base` | `0002_tenant_scoped_idempotency (head)`；`No new upgrade operations detected`；降级成功 |

真实进程 kill/restart **本轮未重跑**：上一轮（基线 `40a629c`）已在临时库完成真实 `taskkill /F` 与重启恢复；本轮 P3 只增加写入时的租约校验，`requeue_expired` / `finalize_job` 的恢复路径未改，并由 22 个 worker 测试覆盖。真实 LLM、浏览器 E2E、Python 3.11 **未验证**（本机 `.venv` 为 3.12.8，`LLM_API_KEY` 为空）；CI 结果在推送后确认。迁移测试与冒烟均使用临时数据库，未修改 `data/profitops.db`。

## 2026-11-01 PR #8 P2 收口复验（Claude Code）

分支 `feat/platform-reliability`，基线 `40a629c`。本轮只修复四项 P2：跨租户幂等键冲突、租户前缀后的列宽、并发手工重试状态回退、`run_analysis` 写入 failed 后 `finalize_job` 前退出的恢复；不新增功能，不修改 RAG、前端页面或执行语义。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests -q` | 227 passed, 0 failed, 0 skipped（120.12s，6 个第三方/适配器 warning） |
| 迁移测试 | `python -m pytest backend/tests/test_migrations.py -q` | 2 passed；旧 SQLite schema 升级后数据保留、列宽 200、复合唯一约束存在、旧全局唯一约束消失、`ix_analyses_tenant_id` / `ix_analyses_status_next_run` / `ix_executions_tenant_id` 保留 |
| PostgreSQL 幂等专项 | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests/test_postgres_idempotency.py -q` | 1 passed；跨租户同存储键、105 字符键、同租户重复键拒绝 |
| PostgreSQL 旧库迁移 | 一次性 schema + 手工旧约束 + `stamp 0001_baseline` + `upgrade head` | 升级成功；旧行保留；`analyses`/`executions` 均为 `VARCHAR(200)` 且带 `(tenant_id, idempotency_key)` 复合唯一；schema 已删除 |
| Alembic 新库 | 临时 SQLite：`python scripts/migrate.py upgrade head` / `current` / `alembic check` | `0002_tenant_scoped_idempotency (head)`；`No new upgrade operations detected`；重建后三个索引均存在 |
| 前端测试 | `npm --prefix frontend test -- --run` | 15 passed（1 个测试文件，8.75s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules，5.41s |
| HTTP 冒烟 | 独立临时 SQLite + 端口 8015 + `LLM_PROVIDER=disabled`：`python scripts/smoke.py --base-url http://127.0.0.1:8015` | 15/15 通过，failed=0 |
| 真实进程 kill/restart | 独立临时 SQLite + 端口 8013/8014；测试专用 `sitecustomize.py` 在 failed 写入后停住，`taskkill /F` 杀掉进程，再启动无补丁进程 | 通过；见下方时间线 |

真实 kill/restart 时间线（临时库，已删除；未修改 `data/profitops.db`）：

1. 14:55:34 创建分析，旧进程 PID 31228 的 worker 抢占 attempt 1。
2. 14:55:39 attempt 1 的 failed 行租约过期，运行中的 worker 通过 `_recover_failed_jobs` 重排为 attempt 2（审计 `analysis.retry_scheduled`，`reason=failed_before_finalize`）。
3. attempt 2 再次写入 failed 后，进程在 `finalize_job` 前被 `taskkill /F /PID 31228` 强制终止；库中保留 `status=failed`、`worker_id` 和过期租约。
4. 14:56:13 新进程 PID 16960 启动，启动恢复把该行重排为 attempt 3（同一审计原因），worker 随后完成分析，最终 `waiting_approval`、`attempts=3/3`、`worker_id=null`、`lease_until=null`、`error=null`。

测试专用补丁只存在于操作系统临时目录，通过 `PYTHONPATH` 加载，未提交、未修改生产代码；它用于把进程稳定停在“failed 已写入、finalize 未执行”的窗口，kill 本身是真实操作系统强制终止。真实 LLM、浏览器 E2E、PostgreSQL 0002 downgrade 本轮未执行；CI 结果在推送后另行确认。Python 3.11 未验证（本机 `.venv` 为 3.12.8）。

## 2026-11-01 可靠性 P1 修复复验（Claude Code）

分支 `feat/platform-reliability`，只修复两个 P1：过期租约遵守 `max_attempts` 并进入死信；推荐建议写入与租约所有权校验处于同一数据库事务。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests -q` | 217 passed, 0 failed, 0 skipped（76.87s） |
| 新增故障测试 | `python -m pytest backend/tests/test_worker.py -q -k 'requeue_expired_dead_letters or create_recommendation_rejects_reclaimed'` | 2 passed |
| Alembic 迁移测试 | `python -m pytest backend/tests/test_migrations.py -q` | 1 passed |
| PostgreSQL 专项 | 既有 3 个 PostgreSQL 测试 + 一次性脚本验证死信与推荐租约守卫 | 3 passed；脚本两项断言通过 |
| 前端测试 | `npm --prefix frontend test -- --run` | 15 passed（1 个测试文件） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules |

真实进程 kill/restart **未验证**：本轮只通过数据库租约过期/回收与旧/新 worker 交错测试模拟故障，没有真实终止操作系统进程后重启。真实 LLM、浏览器 E2E 未在本轮执行。

## 2026-11-01 阶段十一验证（Claude Code）

分支 `feat/agent-evaluation-trace`，工作区包含持久化任务队列、认证与租户隔离、可观测性/限流/熔断、Alembic 基线、离线回放评测和运行手册。以下为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests -q` | 215 passed, 0 failed, 0 skipped（110.94s，1 个第三方 DeprecationWarning） |
| Alembic 迁移测试 | `python -m pytest backend/tests/test_migrations.py -q` | 1 passed |
| 迁移脚本（临时 SQLite） | `python scripts/migrate.py upgrade head` / `current` / `alembic check` / `downgrade base` | 全部通过；`alembic check` 无 drift；降级后仅保留 `alembic_version` |
| PostgreSQL 知识库测试 | 一次性 `postgres:16-alpine` + `PROFITOPS_TEST_POSTGRES_URL=... pytest backend/tests/test_knowledge.py -q` | 44 passed（含 3 项 PostgreSQL 并发/索引测试） |
| 前端测试 | `npm --prefix frontend test` | 15 passed（1 个测试文件） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules |
| RAG 离线评测 | `python backend/evals/rag.py` | hit@1 83.33%、hit@3 100%、no-answer 100%、citation validity 100%，network_calls=0 |
| LLM 回放评测 | `python backend/evals/replay.py` | 4/4 案例通过，network_calls=0 |
| HTTP 冒烟 | 独立临时 SQLite + `python scripts/smoke.py --base-url http://127.0.0.1:8011` | 15/15 通过 |
| Prometheus 指标 | `curl http://127.0.0.1:8010/metrics` | 早先运行已确认；本轮未重复 |
| Python 语法检查 | `python -m py_compile`（迁移与评测脚本） | 通过 |

早先一次运行在未安装 `alembic` 时为 204 passed、4 skipped；安装 `backend/requirements.lock.txt` 后复验为 215 passed、0 skipped。PostgreSQL 使用一次性 `postgres:16-alpine` 容器与独立 schema，结束后删除，未连接或修改本机 Supabase 实例。Python 3.11 未验证（本机 `.venv` 为 3.12.8）；真实 LLM 未调用（`LLM_API_KEY` 为空）；CI 结果尚未观察，不标记为通过。迁移脚本使用临时 SQLite，未修改 `data/profitops.db`。

## 2026-10-31 Docker Compose 实际运行验收（Claude Code）

本机 Docker Desktop 29.5.3 / Compose v5.1.4（WSL2）。Docker Hub 直连受限，构建时通过本机代理拉取基础镜像并传入 BuildKit 代理参数；未修改 `compose.yaml`、Dockerfile 或业务代码。`node:22-alpine` 使用 AWS ECR Public 的官方镜像 `public.ecr.aws/docker/library/node:22-alpine` 后本地打标签。后端启动参数为 `LLM_API_KEY=`、`LLM_PROVIDER=disabled`，不调用真实 LLM。

| 检查 | 命令 | 结果 |
|---|---|---|
| 镜像构建 | `docker compose build --build-arg HTTP_PROXY=... --build-arg HTTPS_PROXY=... --build-arg NO_PROXY=...` | 后端、前端镜像构建成功 |
| 启动 | `LLM_API_KEY= LLM_PROVIDER=disabled docker compose up -d` | db/backend healthy，frontend 启动 |
| 后端冒烟 | `.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8010` | 11/11 通过 |
| nginx 代理冒烟 | `.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8080` | 11/11 通过 |
| 健康检查 | `curl http://127.0.0.1:8010/health` | `database=connected`、`simulation=true`、`llm.configured=false`、`llm.provider=disabled` |
| PostgreSQL | `docker compose exec -T db psql -U profitops_demo -d profitops -tAc 'select version();'` | PostgreSQL 16.15 |
| 前端资源 | `curl -I http://127.0.0.1:8080/assets/index-17EP4-Cu.js` | 200 |
| 容器日志 | `docker compose logs backend/frontend/db` | 无启动错误或 Traceback |

验收后执行 `docker compose down`；PostgreSQL 命名卷 `profitops-ai_profitops_pg` 保留，未删除数据。`data/profitops.db` 未被修改。

## 2026-10-31 Phase 2A：DeepSeek 接入（Claude Code）

分支 `feat/deepseek-llm-agent`，基线 `4285afe`。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 52 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，13.02s） |
| LLM/降级测试 | 同上，`backend/tests/test_llm.py` | 19 项：Provider 成功/重试/鉴权/超时/非法 JSON/截断/空响应/纯文本、环境变量读取、Schema、证据引用、无 Key 降级、失败/超时/Schema 降级、`fail` 策略、LLM 成功后审批与幂等执行 |
| 前端单元/交互 | `npm --prefix frontend test` | 8 passed（Vitest + RTL，24.46s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，14.45s |
| 实际 HTTP 闭环 | 独立数据库 `data/smoke-phase2a-final.db`、端口 8011：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过 |
| 真实浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 2/2 通过（19.8s）；后端强制 `LLM_API_KEY=''`，不消耗真实 Token |
| 真实 DeepSeek 调用 | `.venv/Scripts/python.exe scripts/llm_check.py` | **成功**：`llm_success`，模型 `deepseek-flash`，15467.974 ms，1 次尝试，4801 tokens，证据引用全部有效，退出码 0 |

真实调用详情（模型、Token、完整结构化输出、首次截断失败与修复）见 [LLM_LIVE_CHECK.md](LLM_LIVE_CHECK.md)。摘要：分析状态 `waiting_approval`，`engine=deepseek-llm-v1`，`all_references_valid=true`，未自动批准或执行。

首次真实调用在 `max_tokens=1400` 处被截断，返回 `llm_invalid_json` 并降级；修复为 `max_tokens=3000`、收紧提示词输出长度、记录 `finish_reason`，新增截断回归测试后重跑成功。失败尝试保留在验证记录中，没有伪装成成功。Phase 2B 后续把默认值改为可配置的 `LLM_MAX_TOKENS=4000`，最新评测见 `docs/EVALUATION.md`。

单元测试全部使用 `httpx.MockTransport` 或 `FakeProvider`，没有调用 DeepSeek，也没有消耗真实 Token；只有 `scripts/llm_check.py` 发起了真实调用。测试库使用临时目录或独立 `data/*.db`，未修改 `data/profitops.db`。

## 2026-11-01 Phase 2C：原生 Function Calling（Claude Code）

分支 `feat/native-function-calling`，基线 `origin/master` `1134ff4`。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 102 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，21.80s） |
| 原生工具测试 | 同上，`backend/tests/test_llm_tools.py` | 28 项：`tools`/`tool_choice` 请求、`tool_calls` 解析、多轮调用、工作流轨迹持久化、禁止写/未知/非法参数/越界工具、轮次/次数/重复/预算上限、超时与工具异常降级、最终 JSON/Schema 失败、结果截断、`rules`/`fail` 策略 |
| 前端单元/交互 | `npm --prefix frontend test` | 8 passed（Vitest + RTL，26.62s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，13.63s |
| HTTP 冒烟 | 独立数据库 `data/smoke-phase2c.db`、端口 8011、`LLM_PROVIDER=disabled`：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过 |
| 真实 DeepSeek Function Calling | `.venv/Scripts/python.exe scripts/llm_check.py`（3 次脚本运行，真实 `deepseek-flash`） | 第 1 次第 1 轮返回 2 个工具调用并执行成功，第 2 轮 `llm_network_error` 降级；第 2 次成功但未保存完整元数据；第 3 次 `llm_success`：`native_tools=true`、`termination_reason=final_answer`、`20482.961 ms`、`10706 tokens`、2 个工具调用均成功、`all_references_valid=true` |

原生链路验证了模型返回的 `tool_calls` 会被服务端校验并执行，工具结果以 `role=tool` 回传后模型继续下一轮；也验证了 `create_recommendation`、`simulate_execute_approved_action` 等非允许工具无法执行，越界 period/store 被拒绝，循环上限和失败降级可见。第 3 次真实调用进一步确认：模型实际返回了两个允许列表内的只读工具调用，服务端执行后模型输出合法最终 JSON，全部证据 ID 有效，分析进入 `waiting_approval`，未自动批准或执行。第 1 次真实网络失败也按 `deterministic_fallback` 暴露。完整记录见 [LLM_LIVE_CHECK.md](LLM_LIVE_CHECK.md)。

测试库使用临时目录或独立 `data/*.db`，未修改 `data/profitops.db`。

## 2026-11-01 Phase 2D：Agent 评测与执行轨迹（Claude Code）

分支 `feat/agent-evaluation-trace`，基线 `origin/master` `69aae77`。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 130 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，25.17s） |
| 原生评测测试 | 同上，`backend/tests/test_native_evals.py` | 28 项：fixture 到临时账本可复现、正常多轮、多余/未知/写/审批工具、非法参数、越界、重复、轮次上限、工具失败、非法 JSON、非法证据、数据不完整、负向对照必须整次运行通过、真实 Provider 标签、真实入口授权/预算/未配置、CLI 失败退出码、integrity / expected label 失败与零案例 fail-closed |
| 原生 Tool Calling 离线评测 | `.venv/Scripts/python.exe backend/evals/runner.py native --report data/eval-tmp/native-failclosed.json` | 37 次运行：正常 3/3、负向对照 34/34、安全门禁 37/37；`dataset_passed=true`、`valid_cases=4/4`；`tool_selection_accuracy=1.0`、`tool_argument_validity_rate=0.889`、`task_completion_rate=1.0`、`evidence_reference_validity_rate=0.667`、`degradation_rate=0.833`、不必要调用 3、无效调用 24；`provider_type=fake_tools`，真实网络调用 0 |
| 前端单元/交互 | `npm --prefix frontend test -- --run` | 11 passed（Vitest + RTL，6.11s），含敏感工具参数不回显测试 |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，5.66s |
| HTTP 冒烟 | 独立数据库 `data/smoke-phase2d.db`、端口 8011、`LLM_PROVIDER=disabled`：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过，failed=0 |
| 真实浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 3/3 通过（16.4s）：真实 API 闭环、拒绝分支/手机视图、Agent Trace 桌面与 390px 移动端 |
| Agent Trace 截图 | 同上 | `docs/screenshots/agent-trace.png`、`docs/screenshots/agent-trace-mobile.png` |
| 真实 `native --real` | 未执行 | **未验证**：入口要求显式授权和调用预算，本阶段未消耗真实 DeepSeek Token |

Phase 2D 离线评测的 token 与延迟来自脚本化 `FakeToolProvider` 的固定值，不是真实模型性能；`tool_selection_accuracy` 只统计 3 次正常场景，不包含负向对照。真实浏览器 Trace 测试使用 Playwright 路由注入一条已记录的原生失败轨迹，验证前端渲染与 390px 布局，不代表真实模型调用。

fail-closed 回归：复制案例并破坏 `source_type`、`expected.primary_driver`，或使用空案例目录时，CLI 均返回 1；报告 `dataset_checks.failed_cases` 记录 `case_id`、失败阶段和具体失败检查项。前端测试注入 `api_key`、`Authorization`、`token`、`password` 及非法 `period` / `store_id`，断言页面文本不包含其值，同时保留轮次、成功/失败和耗时展示。

测试数据库为临时目录或独立 `data/*.db`，未修改 `data/profitops.db`；`data/smoke-phase2d.db` 与 `data/e2e.db` 均在 `data/` 下且被 Git 忽略。

## 2026-11-01 RAG 运营知识库（Claude Code）

分支 `feat/agent-evaluation-trace`，在 Phase 2D 工作树上增量实现。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 142 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，34.18s） |
| 知识库测试 | 同上，`backend/tests/test_knowledge.py` | Seed 幂等、相关/无关检索、`no_answer` 清空 hits、approver 创建/停用、analyst 403、重复正文幂等、引用子集校验、提示词隔离、Markdown 切分、OpenAI 兼容 embedding 排序/归一化与 502 失败映射 |
| 前端单元/交互 | `npm --prefix frontend test -- --run` | 15 passed（Vitest + RTL，9.66s），含知识库检索、无答案状态、approver 新增文档、建议引用标记、LLM 知识引用 |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules，4.90s |
| RAG 离线评测 | `.venv/Scripts/python.exe backend/evals/rag.py` | 6 篇文档、8 个查询；hit@1 83.33%、hit@3 100%、no-answer 100%、citation validity 100%；网络调用 0 |
| HTTP 冒烟 | 独立数据库 `data/smoke-rag-20261101.db`、端口 8011、`LLM_PROVIDER=disabled`、空 `LLM_API_KEY`：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 15/15 通过，failed=0；新增知识库 Seed、相关检索、无答案、analyst 写入 403 四项检查 |
| 真实浏览器 E2E | 未执行 | **未验证**：本次未重新运行 Playwright；知识库页面只由 Vitest + RTL 和生产构建覆盖 |
| 真实 embedding 服务 | 未执行 | **未验证**：默认 hashing embedding；OpenAI 兼容 Provider 只用 `httpx.MockTransport` 测试，没有连接真实 Ollama/vLLM/云端端点 |

RAG 评测报告写入 `backend/evals/reports/rag-latest.json`。评测强制 hashing embedding，使用临时 SQLite，不读写 `data/profitops.db`；`hit@1` 未达 100% 的查询是“执行幂等键重复请求怎么办”，top-1 命中广告预算文档、top-2 命中目标文档，报告保留真实排名，没有为提升 hit@1 调参。

开发过程中发现并修复：无关查询“如何预订会议室午餐”最初因中文单字“预/订”误命中广告文档；修复为单字低权重、`KNOWLEDGE_MIN_SCORE` 默认 0.15，并在 `no_answer` 时清空 hits。HTTP 冒烟使用独立 `data/smoke-rag-20261101.db`，未修改 `data/profitops.db`。

## 已验证（2026-10-30 基线）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 33 passed, 0 failed, 0 skipped（1 个第三方 DeprecationWarning） |
| 业务评测 | 同上，`backend/tests/test_evals.py` | 5/5 样本通过：广告驱动、健康增长、履约压力、零基数、退款压力 |
| 前端单元/交互 | `npm --prefix frontend test` | 6 passed, 0 failed（Vitest + RTL） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，图表拆分为懒加载 chunk |
| 前端依赖审计 | `npm --prefix frontend audit --registry=https://registry.npmjs.org` | 0 vulnerabilities |
| Python 依赖一致性 | `.venv/Scripts/python.exe -m pip check` | No broken requirements |
| 一键完整启动 | `.venv/Scripts/python.exe scripts/dev.py`（含 pip 与 `npm ci`） | 安装完成；前端 200，API health 正常；Vite 6.4.5 / Uvicorn 已启动 |
| 实际 HTTP 闭环 | `.venv/Scripts/python.exe scripts/smoke.py` | 11/11 检查通过（空库自动初始化后复跑仍为 11/11） |
| 真实浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 2/2 通过：桌面完整闭环、手机视图与拒绝分支；无页面异常；使用隔离的 `data/e2e.db` |
| 容器配置语法 | `docker compose config --quiet` | 通过 |

## 2026-10-30 接管复验（Claude Code）

接管后重新执行以下命令，结果均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 33 passed，1 个第三方 DeprecationWarning |
| 前端测试 | `npm --prefix frontend test` | 6 passed |
| 生产构建 | `npm --prefix frontend run build` | 通过 |
| 一键启动 | `python scripts/dev.py --skip-install` | 启动成功；`/health` 200、前端 200、summary `anomaly=true` / `bridge_verified=true`；Ctrl+C 后 8010/5180 端口释放 |
| HTTP 冒烟 | 独立数据库 + 8011 端口：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过 |
| 浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 2/2 通过，截图已刷新 |
| Python 依赖 | `.venv/Scripts/python.exe -m pip check` | No broken requirements |
| 前端依赖 | `npm --prefix frontend audit --registry=https://registry.npmjs.org` | 0 vulnerabilities |
| Compose 语法 | `docker compose -f compose.yaml config --quiet` | 通过 |
| Compose 运行 | `docker compose up --build` | 当时未执行：Docker Desktop 引擎不可用；2026-10-31 已补验（见上） |

新增回归覆盖：工具 `policy` 分类与 `deny` 拒绝、API 时间戳显式 UTC、审计按 `request_id` 过滤、Seed 不删除审批/执行/审计、审批页动作预览与审计过滤 UI。冒烟与浏览器测试使用独立数据库（`data/smoke-takeover-2.db`、`data/e2e.db`），未修改 `data/profitops.db` 中的用户运行数据。

## 尚未验证（环境阻碍）

- Docker Compose 已于 2026-10-31 在本机 Docker Desktop 29.5.3 上完成 PostgreSQL 16、后端和 nginx 前端的实际启动验收；后端直连与 nginx 代理各 11/11 通过。其他机器仍需按本文件复验；本机 Docker Hub 直连受限，重新构建时可能需要代理或镜像加速。
- Phase 2C 原生 Function Calling 已对真实 DeepSeek 执行 3 次脚本运行：第 1 次因网络错误降级，第 3 次完整成功并记录了 `tool_calls`、逐轮 Token/耗时和最终输出。样本只有 3 次，不能据此声明真实调用成功率或多轮稳定性。
- Phase 2D 的 `native --real` 真实模型工具评测未执行；当前 37 次结果全部来自 `FakeToolProvider`，只能证明生产工具循环与门禁在合成案例上的行为，不能声明真实模型的工具选择准确率、参数合法率、真实 token 或延迟。
- RAG 的真实 embedding 服务与真实业务语料未验证：默认 hashing embedding 是离线方案，OpenAI 兼容 Provider 只有 MockTransport 测试；6 篇合成 SOP、8 个查询的评测不能声明通用语义检索准确率。PDF/OCR、reranker、文档级 ACL、大规模向量索引未实现。
- 本次 RAG 改动未重新运行 Playwright 浏览器 E2E；知识库页面由 Vitest + RTL 和生产构建覆盖，未在真实浏览器中复验。

浏览器验收不属于未验证项：Playwright + Chromium 真实页面测试 2/2 通过，截图位于 `docs/screenshots/`。

## 环境

Windows 11、Python 3.12.8（venv）、Node 24.16.0、npm 11.13.0、Git 2.40.0、Docker Desktop 29.5.3 / Compose v5.1.4（2026-10-31 已启动并完成容器验收）。默认端口 8010 / 5180；本机 8000 / 5173 被其他进程占用，本项目不修改它们。

## 开发过程中的失败与修复

- 首轮后端测试 28 passed / 1 failed：`test_no_anomaly_does_not_invent_action` 所选的 7 天数据实际也存在轻微利润背离，测试前提错误。改为显式构造无成本、利润随销售增长的数据后重跑通过。没有放宽生产逻辑。
- 首次 npm 安装因本机镜像 TLS `ECONNRESET` 失败，改用官方 registry 后成功；随后升级 Vite/Vitest/Playwright 修复审计漏洞，最终 `npm audit` 为 0。
- 首次 Playwright WebServer 启动失败：Windows 将 Python 相对路径拆成命令；改为绝对路径并加引号后，浏览器测试通过。
- Docker Desktop 启动在 120 秒限制内未就绪，停止尝试；因此当时的容器运行验证保持未完成状态。2026-10-31 再次启动 Docker Desktop 后成功完成 Compose 验收；Docker Hub 直连仍受限，构建时使用本机代理。

最终结果不包含任何伪造或跳过的测试。

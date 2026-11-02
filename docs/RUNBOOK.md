# ProfitOps AI 运行手册

本手册面向本地演示和单机部署。所有执行仍是 simulation，不会调用真实广告、采购或支付系统。生产多副本部署需要额外的共享限流、队列和密钥管理，见文末限制。

## 1. 启动与停止

本地（SQLite，推荐）：

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8010
npm --prefix frontend run dev
```

Docker Compose（PostgreSQL 16 + 后端 + nginx 前端）：

```bash
LLM_API_KEY= LLM_PROVIDER=disabled docker compose up --build -d
python scripts/smoke.py --base-url http://127.0.0.1:8010
docker compose down
```

`/health` 只检查数据库连通性，不返回密钥；`/api/demo` 可匿名读取 demo 租户概览。其余 API 需要 Bearer token 或 `POST /api/auth/login` 换取的 JWT。

## 2. 数据库迁移

Alembic 配置在 `backend/alembic.ini`，环境变量 `PROFITOPS_DATABASE_URL` 决定目标库。新库：

```bash
python scripts/migrate.py upgrade head
```

已由旧版 `initialize_database` 建表、但还没有 `alembic_version` 的库，先备份，再标记基线，然后升级到 head：

```bash
python scripts/migrate.py stamp 0001_baseline
python scripts/migrate.py upgrade head
python scripts/migrate.py current
```

真正的前租户库（没有 `tenants`/`users` 表，没有 `tenant_id`，也没有 `attempts`/`lease_until` 等任务字段）同样走上面的官方路径。`stamp 0001_baseline` 只写版本号、不执行 DDL，因此 `0002_tenant_scoped_idempotency` 会先调用启动兼容迁移的 `upgrade_legacy_schema` 把基线结构补齐，再把 analyses/executions 的 idempotency_key 从全局唯一、`VARCHAR(100)` 改为 `(tenant_id, idempotency_key)` 复合唯一、`VARCHAR(200)`。全过程原地升级：不删业务表、不清库、不要求手工重置，既有 Analysis/Recommendation/Execution/Audit 行与外键关系保留，旧行补齐 `tenant_id='demo'`。

SQLite 的表重建会临时删除被引用的父表；迁移在写入任何 DML 之前于当前连接关闭 `PRAGMA foreign_keys`，行复制完成后外键关系仍然有效，迁移结束可用 `PRAGMA foreign_key_check` 验证（应为空）。PostgreSQL 使用常规 `ALTER TABLE`，无需关闭外键。若历史库存在重复的活跃知识文档，迁移保留最新一行并把其余行 `active=false`，避免新部分唯一索引创建失败。

该约束变更**必须由 Alembic 执行**；应用启动的 `initialize_database` 兼容迁移只补充 `tenant_id`、任务字段和知识库索引，不会修改这两个唯一约束。未执行 0002 的旧库仍会遇到跨租户键冲突和 105 字符键超限。`downgrade` 只用于本地验证：若库中存在跨租户同键行，唯一约束会让降级失败并保留原行；其余情况会删除表，不要对含审批/审计数据的库执行。

SQLite 备份：

```bash
sqlite3 data/profitops.db ".backup 'data/profitops-backup.db'"
```

PostgreSQL 备份（Compose）：

```bash
docker compose exec -T db pg_dump -U profitops_demo profitops > profitops.sql
docker compose exec -T db psql -U profitops_demo profitops < profitops.sql
```

## 3. 可观测性

```bash
curl -s http://127.0.0.1:8010/health
curl -s http://127.0.0.1:8010/metrics
```

关键指标：

- `profitops_http_requests_total{method,path,status}` 与 `profitops_http_request_duration_seconds`：路径使用路由模板，不是原始 UUID。
- `profitops_analysis_jobs_total{event,status,code}`：claimed、recovered、retry_scheduled、dead_letter、finished。
- `profitops_analysis_queue_depth{state="queued|running"}`。
- `profitops_tool_calls_total{tool,outcome,code}`。
- `profitops_llm_requests_total{outcome,code}`、`profitops_llm_circuit_state`（0 closed、1 half-open、2 open）。
- `profitops_rate_limited_total{scope}`。

HTTP 请求指标的 `path` 标签使用路由模板（如 `/api/analyses/{analysis_id}`），未匹配路由固定为 `<unmatched>`，限流器在路由前提前返回的 429 固定为 `<rate_limited>`。标签基数不随客户端路径增长；不要改回原始 URL。

`PROFITOPS_METRICS_ENABLED=false` 会关闭 `/metrics` 并停止记录。日志为 JSON，带 `request_id`；API 错误响应也返回同一个 `request_id`，可用 `GET /api/audit-logs?request_id=...` 追踪。

## 4. 分析任务队列

分析任务持久化在 `analyses` 表。正常状态流转：`queued → running → waiting_approval / completed / insufficient_data / failed`。worker 用条件 UPDATE 抢占并写 `attempts`、`worker_id`、`lease_until`；执行期间心跳续租。

租约是写入许可，不只是抢占标记：创建建议、持久化步骤/结果、写审计前的所有权确认和心跳续租都要求 `status='running'`、`worker_id`、`attempts` 匹配且 `lease_until > 数据库当前时间`（比较在 SQL 中完成，`lease_until == now` 即到期）。因此租约到期后即使还没有新 worker 接管，旧 worker 也不能再写入或续租；心跳发现租约丢失会停止。`finalize_job` 仍会清理到期行并按重试/死信规则处理，恢复语义不变。

排查：

```bash
# 查看积压（SQLite）
sqlite3 data/profitops.db "select status, count(*) from analyses group by status;"
# 查看死信
sqlite3 data/profitops.db "select id, attempts, max_attempts, error from analyses where status='failed' order by updated_at desc limit 20;"
```

- 进程崩溃后，启动或下一轮 poll 会把未耗尽 `max_attempts` 的过期租约改回 `queued`，审计动作 `analysis.recovered`，错误码 `lease_expired`；已耗尽的租约行标记 `dead_letter`，审计动作 `analysis.dead_letter`。
- `run_analysis` 已写入 `failed` 但进程在 `finalize_job` 之前退出时，行上仍有 `worker_id` 和过期租约；恢复扫描会按同样的重试/死信规则处理，审计动作 `analysis.retry_scheduled` 或 `analysis.dead_letter`，错误详情带 `recovered_before_finalize=true`，不会永久停在 failed。
- 可重试错误：`workflow_failed`、`step_timeout`、`database_locked`、`worker_crashed`，按指数退避；达到 `max_attempts` 后标记 `dead_letter`。
- 确定性失败（如 `llm_failed`）不自动重试。人工确认后调用：

```bash
curl -X POST http://127.0.0.1:8010/api/analyses/<id>/retry \
  -H "Authorization: Bearer demo-analyst"
```

只有 `failed` 行可以重试；重试用单条 `status='failed'` 条件 UPDATE 完成，并发重试只有一个成功，running/completed 不会被改回 queued，其余请求返回 409 `analysis_not_retryable`。

worker 线程默认随 API 进程启动。设置 `PROFITOPS_ANALYSIS_WORKER_ENABLED=false` 会同时关闭 API 进程内的轮询线程和请求级 BackgroundTasks：API 只写 `queued` 行，不再自己执行任务。独立 worker 使用同一数据库和同一套环境变量，在项目根目录启动（这样会读取根 `.env`）：

```bash
cd E:/ProfitOps-AI
PYTHONPATH=backend .venv/Scripts/python.exe -m app.worker
```

一次性排空一个任务（适合排障或定时任务）：

```bash
cd E:/ProfitOps-AI
PYTHONPATH=backend .venv/Scripts/python.exe -m app.worker --once
```

`--poll-seconds` 可临时覆盖 `PROFITOPS_ANALYSIS_WORKER_POLL_SECONDS`。worker 启动时会初始化数据库并回收过期租约；停止用 Ctrl+C。API 与 worker 必须指向同一个 `PROFITOPS_DATABASE_URL`，否则任务不会被处理；从其他目录运行 worker 时需显式导出环境变量，不能依赖根 `.env`。SQLite 只适合单机演示；多副本部署应使用 PostgreSQL，并让每个 API 进程都设置 `PROFITOPS_ANALYSIS_WORKER_ENABLED=false`，由独立 worker 进程消费队列。

## 5. LLM 与熔断

未配置 `LLM_API_KEY` 时完全走确定性规则，不发起网络请求。配置后：

- 连续可重试错误（429、5xx、超时、网络、空响应）达到 `PROFITOPS_CIRCUIT_BREAKER_FAILURES`（默认 3）会打开熔断，冷却 `PROFITOPS_CIRCUIT_BREAKER_COOLDOWN_SECONDS`（默认 30s）内直接返回 `llm_circuit_open`。
- 鉴权失败、余额不足、非法 JSON 等确定性错误不计入熔断。
- `LLM_FALLBACK_POLICY=rules` 时分析继续并标记 `deterministic_fallback`；`fail` 时分析失败，错误码 `llm_failed`。`result.llm`、审计和 `/metrics` 都能看到降级/熔断状态。
- 排查真实调用前先确认 `LLM_API_KEY` 只存在于本地环境或 Secret 管理器，绝不写入 Git、镜像或日志。

## 6. 限流与认证

登录、创建分析和其他 API 分别按 IP + Bearer token 哈希限流，默认 10/30/120 次每分钟。超限返回 429 `rate_limited` 和 `Retry-After`。相关变量：`PROFITOPS_RATE_LIMIT_ENABLED`、`PROFITOPS_RATE_LIMIT_REQUESTS`、`PROFITOPS_RATE_LIMIT_WINDOW_SECONDS`、`PROFITOPS_RATE_LIMIT_LOGIN_REQUESTS`、`PROFITOPS_RATE_LIMIT_ANALYSIS_REQUESTS`。

演示账号：`analyst@demo.local` / `approver@demo.local`，密码由 `PROFITOPS_DEMO_ANALYST_PASSWORD`、`PROFITOPS_DEMO_APPROVER_PASSWORD` 控制。非 demo mode 必须设置至少 16 字符的 `PROFITOPS_AUTH_SECRET`；服务端每次请求重新读取用户、角色和租户状态，不信任 token 内的角色声明。

## 7. 镜像与发布检查

CI 的 `containers` job 只构建、不推送：

```bash
docker build -f backend/Dockerfile -t profitops-backend:local .
docker build -f frontend/Dockerfile -t profitops-frontend:local .
```

发布前至少执行：

```bash
python -m pytest backend/tests -q
PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests/test_postgres_idempotency.py -q
npm --prefix frontend test
npm --prefix frontend run build
python backend/evals/rag.py
python backend/evals/replay.py
python scripts/migrate.py upgrade head
python scripts/smoke.py --base-url http://127.0.0.1:8010
```

CI 使用空 `LLM_API_KEY` 和临时测试数据库；真实 LLM 调用必须单独授权并记录次数，不得进入自动 CI。PR 不自动合并，合并前需要 CI 通过和人工审查。

## 8. 已知限制

- 限流桶和指标注册表都是进程内状态；多副本部署需要共享限流与指标聚合。独立 worker 已提供 CLI，但多副本 worker 的并发与指标聚合仍需部署侧验证。
- 没有 OIDC/SSO、密码重置、MFA、密钥轮换、租户管理后台和审计留存策略。
- 没有分布式追踪/OpenTelemetry；日志目前只输出到进程 stdout。
- Alembic 基线只覆盖当前 schema；后续变更必须新增显式 revision，不能修改已应用的基线。
- 超时在步骤结束后检查，不能强制中断阻塞中的同步线程。

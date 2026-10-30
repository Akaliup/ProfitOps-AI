# ProfitOps AI 接管报告与交接说明

日期：2026-10-30  
接管人：Claude Code（唯一主开发 / 技术负责人 / 交付负责人）  
工作区：`E:\ProfitOps-AI`（沿用原项目，不重建、不覆盖、不清理未提交修改）

## 1. 接管结论

磁盘上的项目已经是一个可运行、可测试、可演示的 MVP，不是空壳，也不是只停留在文档阶段。接管后我以实际代码、数据库、测试输出和真实 HTTP/浏览器结果为准重新验证了一遍，并补齐了几处影响“可信 Agent”表达的缺口。

当前状态：**核心业务闭环完整，后端 33 项测试、前端 6 项测试、生产构建、HTTP 冒烟 11 项、Chromium 浏览器 E2E 2 项全部通过。** 唯一明确的环境阻碍是 Docker Desktop 引擎不可用，因此 Compose 的 PostgreSQL 容器运行路径仍未实际验证（配置语法已通过）。

## 2. 接管方法与事实来源

先只读检查，再动手：

- 读取 `AGENTS.md`、`TASKS.md`、`README.md`、`docs/CONTRACT.md`、`docs/ARCHITECTURE.md`、`docs/TEST_REPORT.md`。
- 逐个阅读后端 `models / schemas / metrics / workflow / tools / services / seed / security / main / views / audit`。
- 逐个阅读前端 `App.tsx / api.ts / types.ts / format.ts / TrendChart.tsx` 与测试、E2E。
- 只读检查 `data/profitops.db`（未启动服务写入、未清空）：2 店铺、3 SKU、1920 订单、360 条每日经营记录、2 次分析/执行、22 条审计，Seed 版本 `demo-60d-v1`。
- 检查 Git：接管时当前分支 `master`，**没有任何 commit**，所有项目文件均为未跟踪状态。没有使用 `reset --hard`、`clean` 等破坏性命令。本轮结束时已按用户明确要求建立首个基线（见第 10 节）。
- 未读取或输出 `.env` 中的任何密钥；`.env.example` 只包含公开的本地演示值。
- 所有新增验证使用独立数据库（`data/smoke-takeover-2.db`、`data/e2e.db`），未触碰用户运行库。

## 3. 交接时已经完成的工作

以下均以磁盘实现为准，不依赖前任 Agent 的聊天记录：

### 后端

- SQLAlchemy 模型：店铺、SKU、订单、每日经营记录、分析、建议、执行、审计、Schema/Seed 版本。
- 60 天确定性 Seed（2026-09-01 至 2026-10-30，2 店铺 × 3 SKU），覆盖销售、折扣、退款、采购、广告、履约、平台费、其他可变成本、库存与在途库存。
- 整数美分利润口径：净销售额、净利润、利润率、环比、8 项利润贡献桥；贡献项加总必须等于净利润变化。
- 数据完整性：按“店铺 × SKU × 日期”检查每日经营记录缺失/不完整；不完整时 `insufficient_data`，不生成建议。
- LangGraph 分析图 7 个节点：接收请求 → 读取数据 → 计算利润 → 归因 → 验证证据 → 创建建议 → 等待审批；每步写入数据库快照。
- LangGraph 执行图 3 个节点：读取审批状态 → 事务内幂等模拟执行 → 保存结果。
- 统一 Tool Registry：6 个工具，Pydantic 输入/输出 Schema，角色与副作用分类。
- 审批状态机：`proposed → pending_approval → approved → executing → executed`，以及 `rejected`、`execution_failed`；条件 UPDATE 抢占状态。
- 幂等：分析请求键、执行请求键全局唯一；同一执行键重复调用返回同一 execution 且 `replayed=true`；不同建议复用执行键返回 409。
- 审计：分析请求/完成/失败、建议提出/待审批/批准/拒绝、执行开始/成功/失败/回放、被拒绝的请求；带请求 ID。
- 演示身份：服务端 Bearer token 映射 analyst / approver，不信任 `X-Role` / `X-Actor`。
- 统一错误格式 `{error:{code,message,details},request_id}` 与 `X-Request-ID`。
- 重启恢复：启动时将遗留 `queued/running` 分析标记为 `failed`（`restart_interrupted`）。
- 业务评测样本：广告驱动、健康增长、履约压力、零基数、退款压力 5 个。

### 前端

- 四个页面：经营总览、Agent 工作台、审批与执行、审计日志。
- 真实 API 数据，无静态经营假数据；加载、空数据、API 错误（带请求 ID）、分析轮询、审批、执行、幂等回放、手机布局。
- 利润归因、趋势图 + 可切换数据表、SKU 明细、证据表、风险/假设/待补充信息。
- 深色/浅色主题、响应式布局、演示身份切换。
- Vitest + RTL 交互测试；Playwright 真实浏览器 E2E。

### 交付与文档

- 一键启动 `python scripts/dev.py`（创建 venv、安装依赖、初始化 60 天数据、启动前后端）。
- Docker Compose（PostgreSQL 16 + 后端 + nginx 前端）配置。
- HTTP 冒烟脚本、浏览器验收脚本、契约/架构/验证记录/截图。

## 4. 实际能够运行的功能（本次复验）

| 能力 | 验证方式 | 结果 |
|---|---|---|
| 后端启动、建表、自动 Seed | 独立数据库 + uvicorn | 通过 |
| 经营指标、利润桥、异常检测 | `GET /api/dashboard/summary`、pytest | 通过 |
| Agent 分析全流程 | `POST /api/analyses` + 轮询 | `waiting_approval` |
| 数据不足停止建议 | pytest | `insufficient_data` |
| 越权审批拦截 | analyst token 调 approve | 403 |
| 审批 → 模拟执行 → 幂等回放 | HTTP 冒烟 / E2E | 同一 execution ID，`replayed=true` |
| 审计按对象/请求 ID 追踪 | `GET /api/audit-logs?object_id=...` / `?request_id=...` | 通过 |
| 前端完整闭环 | Chromium E2E | 2/2 |
| 手机视图 | Playwright 390×844 | 无横向溢出 |
| 生产构建 | `tsc -b && vite build` | 通过 |

## 5. 真实测试结果（2026-10-30 复验）

```
.venv/Scripts/python.exe -m pytest backend/tests -q
33 passed, 1 warning in 8.90s

npm --prefix frontend test
Test Files 1 passed (1)
Tests 6 passed (6)

npm --prefix frontend run build
✓ built in 4.65s

.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011
passed: 11, failed: 0

.venv/Scripts/python.exe scripts/browser_test.py --skip-install
2 passed (11.8s)

python scripts/dev.py --skip-install
启动成功；/health 200、前端 200、summary anomaly=true / bridge_verified=true；Ctrl+C 后端口释放
（使用现有 data/profitops.db；启动前后 analyses/recommendations/executions/audit_logs 计数不变）

.venv/Scripts/python.exe -m pip check
No broken requirements found.

npm --prefix frontend audit --registry=https://registry.npmjs.org
found 0 vulnerabilities

docker compose -f compose.yaml config --quiet
通过
```

详细记录见 `docs/TEST_REPORT.md`。测试没有跳过、没有伪造；Docker 运行项明确标记为未验证。

## 6. 尚未实现或未验证

### 环境阻碍

- **Docker Compose 实际运行**：本机 Docker Desktop 引擎不可用（`dockerDesktopLinuxEngine` 未启动，尝试启动后仍无法连接）。因此 PostgreSQL 容器初始化、容器内并发、nginx 反向代理没有实际跑过；只验证了 `docker compose config` 语法。
- 由于没有 Docker，PostgreSQL 上的条件 UPDATE / 唯一约束并发行为未在本机实测；SQLite 并发测试已通过。

### 明确未实现

- 真实 MCP Server（README 未声称已接入）。
- 付费 LLM Provider；当前是确定性规则引擎，不需要 API Key。
- 生产身份（OIDC）、租户隔离、权限后台、速率限制、密钥轮换。
- Alembic 增量迁移；当前是 `create_all` + 版本化 Seed。
- 持久化任务队列；分析使用 FastAPI BackgroundTasks，进程重启会把中断分析标记为 failed。
- 数据导入、汇率、多币种、税项。
- 分析/建议/审计列表的单租户演示视图（所有已认证身份可见全部数据）。

## 7. 当前最严重的问题（按风险排序）

1. **Git 基线已建立**：接管时仓库零 commit、全部文件未跟踪；用户随后明确要求提交，已在 `master` 建立根提交（67 个文件）。后续提交仍须遵守 `AGENTS.md`：由用户明确要求后才提交。
2. **Docker/PostgreSQL 路径未实测**：这是交付清单里唯一明确未完成的运行项，也是面试时最容易被追问的环境问题。需要一台 Docker 可用的机器跑一次 `docker compose up --build`。
3. **演示认证不是生产安全方案**：`/api/demo` 会返回公开演示 token；没有租户隔离、限流、审计防篡改。文档已明确标注，不能把它说成生产能力。
4. **规则引擎不是 LLM**：建议文案和归因由确定性规则生成。这是为了可复现和“不让 LLM 编数字”，但如果面试官期待“LLM Agent”，需要主动解释边界与可替换 Provider 的设计。
5. **没有增量迁移**：修改模型后旧数据库不会自动加列/加约束；当前依赖删除数据库重建。生产化前必须补 Alembic。
6. **后台任务是进程内的**：多 worker 部署时 BackgroundTasks 不会跨进程协调；重启会中断分析。MVP 可接受，生产需要队列 + worker。
7. **单租户视图**：所有身份共享分析/建议/审计列表；没有按 actor 或 tenant 过滤。

## 8. 下一步开发顺序（建议）

### P0：交付基线

1. 首个 Git 基线已完成（`master` 根提交，2026-10-30，用户明确要求）。
2. 在 Docker 可用的机器执行 `docker compose up --build`，跑 `scripts/smoke.py` 与浏览器 E2E，补 `docs/TEST_REPORT.md`。

### P1：可信度与可维护性

3. 引入 Alembic，建立 schema v2 迁移；把 Seed 版本与 schema 版本解耦。
4. 把分析任务改为持久化队列（如 Redis/RQ 或数据库轮询 worker），保留现有状态机与恢复语义。
5. 增加按 actor/tenant 的数据过滤与授权测试；为生产身份预留 OIDC 接口。
6. 增加请求体大小限制、速率限制、CORS/安全响应头。

### P2：Agent 能力

7. 抽象 `ExplanationProvider` 接口：默认规则实现，可选接入真实 LLM；LLM 只负责解释与文案，数字仍来自确定性工具。
8. 增加第二种演示场景（退款/履约驱动），让规则引擎的泛化能力在 UI 上可见。
9. 增加 MCP Server（如果面试岗位强调工具协议），复用现有 Tool Registry 的 Schema。
10. 增加利润计算的属性测试与更多边界样本。

## 9. 关键文件地图

```
backend/app/main.py        FastAPI 路由、中间件、统一错误、审计过滤
backend/app/models.py      数据库模型与约束
backend/app/schemas.py     Pydantic 契约（金额、周期、工具输入输出）
backend/app/metrics.py     确定性利润、环比、贡献桥、数据完整性
backend/app/workflow.py    LangGraph 分析图 / 执行图、步骤快照、重启恢复
backend/app/tools.py       Tool Registry、角色/策略、6 个工具
backend/app/services.py    建议生成、审批状态机、幂等模拟执行
backend/app/seed.py        60 天确定性演示数据
backend/app/security.py    演示 Bearer 身份与服务端角色
backend/app/views.py       API 序列化（UTC 时间戳）
backend/app/audit.py       审计写入
backend/tests/             单元、集成、工作流、并发、评测
frontend/src/App.tsx       四个页面与交互
frontend/src/api.ts        真实 API 客户端
frontend/src/types.ts      与后端契约对应的 TS 类型
frontend/e2e/              Playwright 真实浏览器验收
scripts/dev.py             一键启动
scripts/smoke.py           实际 HTTP 闭环
docs/CONTRACT.md           API/数据库/状态机契约
docs/ARCHITECTURE.md       架构与关键决策
docs/TEST_REPORT.md        实际验证记录
docs/INTERVIEW.md          面试讲解与高频追问
```

## 10. 本轮接管变更（2026-10-30）

- `backend/app/tools.py`：工具显式声明 `policy`（safe / ask / deny）；注册表拒绝 deny；`/api/tools` 暴露策略。
- `backend/app/views.py`：所有 API 时间戳统一为 UTC ISO 8601（`Z`），消除 SQLite/PostgreSQL 输出差异。
- `backend/app/main.py`：`GET /api/audit-logs` 新增 `request_id` 精确过滤。
- `backend/tests/test_workflow.py`：新增 policy/deny 与 UTC 时间戳测试。
- `backend/tests/test_approval.py`：新增审计 request_id 过滤、Seed 保留审批/执行/审计测试。
- `frontend/src/App.tsx`：审批页新增“待执行的模拟动作”预览；审计页新增请求 ID 过滤。
- `frontend/src/api.ts`：审计查询支持 request_id。
- `frontend/src/App.test.tsx`：新增动作预览与审计过滤测试（6 项）。
- `frontend/src/styles.css`：审计过滤输入样式。
- `docs/CONTRACT.md`、`docs/ARCHITECTURE.md`、`docs/TEST_REPORT.md`、`TASKS.md`、`README.md`：同步契约、验证记录与交付状态。
- 新增本文件与 `docs/INTERVIEW.md`。
- 按用户明确要求建立首个 Git 基线：`master` 根提交，67 个文件；`.env`、`data/`、`node_modules` 等运行文件保持忽略。

## 11. 运行与演示入口

```bash
python scripts/dev.py
```

- 工作台：<http://127.0.0.1:5180>
- API 文档：<http://127.0.0.1:8010/docs>
- 健康检查：<http://127.0.0.1:8010/health>

演示身份：`demo-analyst`（分析师）/ `demo-approver`（审批负责人）。所有执行均为 simulation，不调用真实广告、采购或支付系统。

完整演示脚本与面试问答见 `docs/INTERVIEW.md`。

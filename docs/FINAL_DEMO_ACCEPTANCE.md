# ProfitOps AI 最终端到端模拟验收报告

- 验收日期：2026-11-02（本地时间，UTC+8）；对应 UTC 2026-11-01 19:35–19:53
- 仓库：<https://github.com/Akaliup/ProfitOps-AI>
- 分支：`feat/final-agent-validation`
- 原始验收提交：`7aff53bd1dd5d052918b57d79558afed433fd697`；本轮云 Compose 交付提交见最新 Git 历史
- PR：<https://github.com/Akaliup/ProfitOps-AI/pull/9>，状态 open；CI 结果以最新推送提交为准，未自动合并
- 验收方式：真实启动 FastAPI、Vite 前端和 Worker；使用 Playwright Chromium 操作真实页面；使用独立临时 SQLite 数据库；未读取或修改正式 `data/profitops.db`
- 本轮约束：`simulation=true`、LLM Provider 禁用、不调用付费模型、不连接广告/采购/ERP/支付平台

> **重要边界**：本轮是离线模拟验收。`model_calls=0`、`provider_requests=0`、`cost.status=no_data` 是真实结果，不代表 DeepSeek 已在本轮被验证。此前真实模型评测是另一项独立证据，不能混入本轮 PASS 结论。BGE-M3 真实检索仍为既有 BLOCKED 状态，本轮未改动、未重新验证。
>
> **修复复验（2026-11-02）**：原始验收发现的 P1（独立 worker 模式）和 P2（运维窗口竞态、任务状态明细）已修复并通过自动化与隔离运行复验，见第 15 节。第 7.3、7.4、12 节保留原始缺陷记录，并标注修复状态；PostgreSQL 单容器与云端单机 Compose 已在本机隔离环境复验，真实云服务器、真实 DeepSeek、BGE-M3 和多副本仍未验证。

## 0. 结论摘要

| 项目 | 结果 | 说明 |
|---|---|---|
| 端到端业务闭环（异常→Agent→审批→模拟执行→审计） | **PASS** | 真实浏览器完成，执行记录唯一，`external_calls=0` |
| 场景 A：利润异常分析 | **PASS** | 销售额 +46.15%，净利润 -178.48%，广告支出 +497.24% |
| 场景 B：数据不完整 | **PASS** | `insufficient_data`，无建议、无执行 |
| 场景 C：Worker 崩溃与租约恢复 | **PASS（有边界）** | 租约 5s，重启后 attempts 2/3，恢复为 `waiting_approval`；崩溃注入点在 claim 之后、LangGraph 之前 |
| Agent 运维看板核心数据 | **PASS** | 任务、失败、LLM 运行、Token/成本、错误、Trace 跳转均可见；成本真实显示“暂无数据” |
| 运维 24h/72h/168h 过滤 | **PASS** | 三个窗口边界正确，均返回 200 |
| 运维窗口快速切换 | **PASS（修复后复验）** | 原始验收为 FAIL（P2）；`loadOps` 已加请求序号保护，旧响应不能覆盖当前窗口 |
| 运维任务状态明细 | **PASS（修复后复验）** | 原始验收为 FAIL（P2）；`by_status` 现包含全部 10 个分析状态，且各项之和等于 `jobs.total` |
| 安全与权限 | **PASS** | 401/403/404/409 均符合预期，服务端角色校验有效 |
| 审批与执行幂等 | **PASS** | 同键重放返回同一 execution_id；新键也不会产生第二条执行记录 |
| 无真实外部副作用 | **PASS** | `simulation=true`、`external_calls=0`、`side_effect_count=1`（仅数据库模拟记录） |
| 浏览器阻塞错误 | **PASS** | 验收脚本 `consoleErrors=[]`、`pageErrors=[]` |
| 8 张演示截图 | **PASS** | 均来自本轮真实页面，路径见第 10 节 |
| 回归验证 | **PASS** | pytest 263 passed / 6 skipped；Vitest 17 passed；build 成功；smoke 15/15（修复后复验） |
| 独立 Worker 运行模式 | **PASS（修复后复验）** | 原始验收为 FAIL（P1）；关闭 API 内 worker 后任务保持 queued，`python -m app.worker` 可独立消费 |
| 真实 DeepSeek 调用 | **SKIP** | 本轮按约束禁用，未调用、未计费、未验证 |
| BGE-M3 真实检索 | **BLOCKED（沿用既有结论）** | 本轮使用 hashing embedding，不重新评估 BGE-M3 |
| PostgreSQL/Docker 路径 | **PASS（修复后复验）** | 原始验收为 SKIP；已在隔离 `postgres:16-alpine` 容器上跑通 6 个 PostgreSQL 专项测试 |
| 云端单机 Compose 本地验收 | **PASS** | 独立 `profitops-cloud-verify` 栈完成十项验收；真实云服务器部署为 SKIP/BLOCKED（无凭据），见第 16 节 |

**冻结判断**：

- **最终演示冻结：满足。** 业务闭环、权限、幂等、审计、截图、独立 worker 和回归均通过；运维窗口切换与任务状态明细已修复复验。
- **生产上线冻结：未满足。** P1/P2 已修复，PostgreSQL 单容器与云端单机 Compose 已在本机隔离环境复验；但真实云服务器、真实 LLM、多副本和 BGE-M3 仍未验证。
- **P0：无。**

## 1. 环境与版本

| 项目 | 值 |
|---|---|
| 操作系统 | Windows 11 Home China 10.0.26200 |
| Git | 2.40.0.windows.1 |
| Python | 系统 3.14.5；验收使用 `.venv` 的 3.12.8（满足 Python 3.11+） |
| Node.js / npm | v24.16.0 / 11.13.0 |
| Docker | Docker 29.5.3 / Compose v5.1.4；本轮使用隔离 Compose 栈 `profitops-cloud-verify`（PostgreSQL 16 + backend + worker + frontend） |
| 后端 | FastAPI + SQLAlchemy 2.0 + LangGraph |
| 前端 | React 19 + TypeScript + Vite 6.4.5 |
| 数据库 | SQLite（主业务验收，独立临时文件）；PostgreSQL 16（云端 Compose 验收，隔离命名卷） |
| 浏览器 | Playwright Chromium 1248 headless shell |
| 启动脚本 | `scripts/dev.py`（一键启动）、`scripts/smoke.py`（HTTP 冒烟）、`compose.yaml`（本地容器）、`compose.cloud.yaml`（云端单机覆盖） |
| 服务拓扑 | API A `127.0.0.1:8020` / Web A `127.0.0.1:5190`；API B `127.0.0.1:8021` / Web B `127.0.0.1:5191`；API C `127.0.0.1:8022` |
| 临时数据库 | `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/{a,b,c,d,e}.db` |
| 正式数据库 | `data/profitops.db` 存在（745,472 bytes，mtime 2026-10-30 15:55:47 +08:00，早于本轮验收）；本轮未读写或清空 |

验收期间使用的进程（快照；标注“已停止”的不再运行）：

| 服务 | PID | 地址 | 说明 |
|---|---:|---|---|
| API A（场景 A） | 11488 | `127.0.0.1:8020` | in-process Worker，LLM disabled |
| Web A | 32956 | `127.0.0.1:5190` | Vite 代理到 8020 |
| API B（场景 B） | 20840 | `127.0.0.1:8021` | in-process Worker，LLM disabled |
| Web B | 31632 | `127.0.0.1:5191` | Vite 代理到 8021 |
| API C（场景 C） | 15492 | `127.0.0.1:8022` | in-process Worker 关闭，lease 5s |
| Worker 1（场景 C） | 14848（自报）/ 17796（Popen） | 临时 harness | 在 claim 后被强制终止 |
| Worker 2（场景 C） | 23632（自报）/ 30276（Popen） | 临时 harness | 恢复并完成任务后被终止 |
| Smoke API | 26312 | `127.0.0.1:8010` | 独立 `d.db`，跑完 15/15 后已停止 |
| Worker-disabled API | 12444 | `127.0.0.1:8023` | 独立 `e.db`，用于验证 P1 后已停止 |

> Worker 的“自报 PID”来自 harness 的 `os.getpid()`，也是数据库 `worker_id` 的后缀；`Popen` PID 是 orchestrator 观察到的进程号。本次环境中两者不同，验收以 harness 自报值为准，Popen PID 仅用于说明 orchestrator 启动的是哪个进程。

`/health` 实际响应（API A）：

```json
{"status":"ok","database":"connected","simulation":true,"engine":"deterministic-rules-v1","llm":{"configured":false,"provider":"disabled","model":"deepseek-flash"},"knowledge":{"provider":"hashing","model":"hashing-v1","remote":false,"seed_error":null}}
```

## 2. 启动命令

以下命令均在 Git Bash 中执行。环境变量只对当前进程生效，不写入 `.env`。

> 官方推荐的一键启动是 `python scripts/dev.py`，但它默认使用 `data/profitops.db`。为保护正式库，本轮按同一启动路径拆成 API/Web 两个命令，并显式指定独立临时数据库；`scripts/dev.py --skip-install --api-port 8020 --web-port 5190` 是等价的一键方式（默认库仍为 `data/profitops.db`，不要用于本轮验收）。

### 2.1 API A（场景 A）

```bash
cd E:/ProfitOps-AI/backend
PROFITOPS_DATABASE_URL="sqlite:///C:/Users/lz/AppData/Local/Temp/profitops-final-demo/a.db" \
LLM_PROVIDER=disabled PROFITOPS_LLM_API_KEY="" \
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8020
```

### 2.2 Web A（场景 A）

```bash
cd E:/ProfitOps-AI/frontend
PROFITOPS_WEB_PORT=5190 PROFITOPS_API_TARGET=http://127.0.0.1:8020 npm run dev
```

### 2.3 API B / Web B（场景 B）

```bash
cd E:/ProfitOps-AI/backend
PROFITOPS_DATABASE_URL="sqlite:///C:/Users/lz/AppData/Local/Temp/profitops-final-demo/b.db" \
LLM_PROVIDER=disabled PROFITOPS_LLM_API_KEY="" \
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8021
```

```bash
cd E:/ProfitOps-AI/frontend
PROFITOPS_WEB_PORT=5191 PROFITOPS_API_TARGET=http://127.0.0.1:8021 npm run dev
```

### 2.4 API C（场景 C）

```bash
cd E:/ProfitOps-AI/backend
PROFITOPS_DATABASE_URL="sqlite:///C:/Users/lz/AppData/Local/Temp/profitops-final-demo/c.db" \
LLM_PROVIDER=disabled PROFITOPS_LLM_API_KEY="" \
PROFITOPS_ANALYSIS_WORKER_ENABLED=false PROFITOPS_ANALYSIS_LEASE_SECONDS=5 \
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8022
```

### 2.5 Worker

默认模式下，Worker 由 FastAPI lifespan 启动，不需要额外进程：

```python
# backend/app/main.py 的 lifespan 中：
if settings.analysis_worker_enabled:
    worker = AnalysisWorker(factory, engine, registry, settings, llm)
    worker.start()
```

场景 C 需要一个可被单独终止的 Worker。原始验收时仓库没有独立 worker CLI，因此使用**临时验收 harness**（不进入仓库、不修改生产代码）；修复后已新增 `python -m app.worker`，见第 15 节：

```bash
cd C:/Users/lz/AppData/Local/Temp/profitops-final-demo
PROFITOPS_DATABASE_URL="sqlite:///C:/Users/lz/AppData/Local/Temp/profitops-final-demo/c.db" \
PROFITOPS_ANALYSIS_LEASE_SECONDS=5 LLM_PROVIDER=disabled PROFITOPS_LLM_API_KEY="" \
PROFITOPS_ACCEPTANCE_CLAIM_DELAY=8 \
E:/ProfitOps-AI/.venv/Scripts/python.exe worker_runner.py
```

`worker_runner.py` 只调用项目已有的 `requeue_expired`、`claim_next_job`、`process_claimed_job`，不是生产代码。

## 3. 演示数据来源

- 代码：`backend/app/seed.py`
- 版本：`demo-60d-v1`
- 区间：`2026-09-01` 至 `2026-10-30`，UTC 业务日，两端包含
- 规模：2 个店铺 × 3 个 SKU × 60 天 = 360 条每日经营记录；当前周期 2026-10-01–2026-10-30，上一周期 2026-09-01–2026-09-30
- 数据特征：10 月广告支出显著抬升，制造“销售额增长、净利润下降”的利润异常；所有金额为 USD 整数美分
- 幂等：`seed_demo` 通过 `SeedVersion` 保证重复执行不重复插入，不删除审批/审计历史
- 场景 B 的缺失数据由独立临时库 B 的验收 fixture 删除 3 条 `2026-10-30/us-store` 每日经营记录构造，未改动种子逻辑

## 4. 场景 A：利润异常 → Agent → 人工审批 → 模拟执行 → 审计

### 4.1 经营总览

页面：`http://127.0.0.1:5190/`，默认身份 `demo-analyst`。

| 指标 | 上一周期（2026-09-01–09-30） | 当前周期（2026-10-01–10-30） | 变化 |
|---|---:|---:|---:|
| 销售额 | 3,120,000 美分（$31,200） | 4,560,000 美分（$45,600） | +46.15% |
| 净利润 | 726,700 美分（$7,267） | -570,300 美分（-$5,703） | -178.48% |
| 净利润率 | 24.82% | -13.84% | -38.66 pp |
| 广告支出 | 271,500 美分（$2,715） | 1,621,500 美分（$16,215） | +497.24% |
| 订单数 | 780 | 1,140 | +46.15% |

利润桥最大负向贡献项为广告支出 `-1,350,000` 美分；8 项贡献合计与净利润变化 `-1,297,000` 美分一致，`bridge_verified=true`。总览页显示异常横幅“销售在增长，利润却没有跟上。”。

截图：[01-overview.png](screenshots/final-demo/01-overview.png)

### 4.2 创建分析任务

浏览器点击“启动 Profit Agent”。实际请求：

```http
POST /api/analyses
Authorization: Bearer demo-analyst
Idempotency-Key: <浏览器随机 UUID>
Content-Type: application/json

{"start_date":"2026-10-01","end_date":"2026-10-30"}
```

响应 `202`，关键字段：

```json
{"id":"6b4d5b58-c29a-44fb-a71a-6c387cc7048e","status":"queued","request":{"period":{"start_date":"2026-10-01","end_date":"2026-10-30"},"store_id":null},"request_id":"47c83851-b281-4d3d-bbdd-6e522a4bb22f","attempts":0,"max_attempts":3}
```

状态变化：`queued → running → waiting_approval → approved → executed`。分析行 `attempts=1/3`。

### 4.3 LangGraph 执行步骤

页面 `.workflow-panel` 展示了 10 个业务步骤；下列耗时来自 `analysis.steps`：

| # | 步骤 | 状态 | 耗时 |
|---:|---|---|---:|
| 1 | `receive_request` | completed | 3.6 ms |
| 2 | `get_sales_metrics` | completed | 75.4 ms |
| 3 | `calculate_profit` | completed | 4.2 ms |
| 4 | `analyze_profit_drivers` | completed | 8.0 ms |
| 5 | `verify_evidence` | completed | 9.0 ms |
| 6 | `retrieve_knowledge` | completed | 32.9 ms |
| 7 | `llm_root_cause_analysis` | completed | 12.4 ms |
| 8 | `verify_llm_evidence` | completed | 6.0 ms |
| 9 | `create_recommendation` | completed | 22.0 ms |
| 10 | `wait_for_approval` | completed | 6.5 ms |

后续审批/执行还会追加 `get_approval_status`、`simulate_execute_approved_action`、`save_execution_result` 步骤；每次幂等重放都会追加一组，详见第 12 节。

截图：[02-agent-workflow.png](screenshots/final-demo/02-agent-workflow.png)

### 4.4 Agent 结论、证据与知识检索

- 结论：`销售额上涨、净利润下降。广告支出增加是最大负向会计贡献项；这是利润桥归因，不代表已证明业务因果。`
- 主驱动：`ad_spend`，来源 `daily_operations.ad_spend`，贡献 `-1,350,000` 美分
- 数据覆盖：`360 / 360`，`missing_records=0`，`complete=true`
- 证据 ID（8 个，全部可追溯）：`ev-ad_spend`、`ev-cogs`、`ev-fulfillment`、`ev-platform_fees`、`ev-discounts`、`ev-refunds`、`ev-other_costs`、`ev-sales`
- 每个证据包含 `metric`、`current`、`previous`、`contribution`、`period`、`previous_period`、`store_id`、`currency`、`source`、`formula`
- 知识检索返回 5 个 chunk：

| rank | chunk_id | 标题 / 小节 | 分数 |
|---:|---|---|---:|
| 1 | `a2a27bdc-3d00-4483-b4ad-30b4f700c972` | 广告预算调整与审批规则 / 审批流程 | 0.2752 |
| 2 | `c477f09c-ddbd-47b9-a219-f545fedbf7e8` | 广告预算调整与审批规则 / 触发条件 | 0.2702 |
| 3 | `536251be-c5a4-446c-96bd-0a4988f05604` | 履约成本异常排查 / 触发条件 | 0.2159 |
| 4 | `3ee42557-4814-4da0-af58-73c4c3b66cf3` | 数据完整性与缺失处理 / 证据要求 | 0.2006 |
| 5 | `1b701bd0-b1af-411a-b437-312ee3c8c99b` | 广告预算调整与审批规则 / 禁止事项 | 0.1846 |

**知识引用口径**：本轮 LLM disabled，`llm.status=deterministic_fallback`、`reason=not_configured`，因此推荐体中的 `cited=false`。页面展示的是“检索到的知识片段/chunk_id”，不是模型生成的引用；不能宣称“模型已引用知识”。检索本身是真实执行的。

截图：[03-agent-result.png](screenshots/final-demo/03-agent-result.png)、[04-knowledge-citations.png](screenshots/final-demo/04-knowledge-citations.png)

### 4.5 经营建议

- 建议 ID：`a87b7dc2-f0c7-4f98-9c80-122c9487e947`
- 标题：`TRAVEL-BAG-01 · 广告预算优化模拟`
- 建议：针对 `TRAVEL-BAG-01` 将参考日广告预算下调 15%，先进行受控模拟并在 7 天后复核
- 动作：`adjust_ad_budget`，`baseline_daily_budget=25550` 美分（$255.50），`proposed_daily_budget=21717` 美分（$217.17）
- 条件性影响：未来 30 天利润改善情景 `57,495–114,990` 美分（$574.95–$1,149.90），原文标注“这是情景测算，未证明广告弹性，也不是已实现利润”
- 风险：降低广告预算可能减少流量和转化，实际利润可能不升反降；会计贡献只解释利润变化，不能替代因果实验
- 缺失信息：广告活动级 ROAS、边际获客成本与转化弹性；自然流量占比与库存补货周期

### 4.6 人工审批与模拟执行

审批页截图（待审批状态）：[05-approval.png](screenshots/final-demo/05-approval.png)

实际操作与结果：

| 操作 | 身份 | 结果 |
|---|---|---|
| 分析师直接批准 | `demo-analyst` | `403 forbidden` |
| 审批人未批准先执行 | `demo-approver` | `409 not_approved` |
| 审批人批准 | `demo-approver` | `200`，状态 `approved`，理由“已复核利润桥、广告贡献与知识片段，批准受控模拟” |
| 重复批准 | `demo-approver` | `409 invalid_transition`，只有待审批建议可以批准/拒绝 |
| 分析师执行 | `demo-analyst` | `403 forbidden` |
| 审批人模拟执行 | `demo-approver` | `200`，状态 `executed` |

执行记录：

```json
{
  "execution_id": "655879de-c26f-4f94-b1fd-0bd3e33062dc",
  "recommendation_id": "a87b7dc2-f0c7-4f98-9c80-122c9487e947",
  "status": "executed",
  "simulation": true,
  "result": {
    "simulation": true,
    "action_type": "adjust_ad_budget",
    "sku": "TRAVEL-BAG-01",
    "before": {"daily_budget": 25550},
    "after": {"daily_budget": 21717},
    "currency": "USD",
    "side_effect_count": 1,
    "external_calls": 0
  }
}
```

截图：[06-execution.png](screenshots/final-demo/06-execution.png)

### 4.7 幂等验证

- 使用与前端相同的 `Idempotency-Key: web-execute-<recommendation_id>` 再次请求：`200`，`replayed=true`，`execution_id` 仍为 `655879de-c26f-4f94-b1fd-0bd3e33062dc`
- 使用全新的 `Idempotency-Key` 再次请求已执行建议：仍返回同一条执行记录，`replayed=true`，没有创建第二条 `Execution`
- 数据库/页面核对：该建议只有 1 条执行记录，执行记录的前后预算值保持不变（25550 → 21717），`external_calls=0`
- 审计记录每次重放写 `execution.replayed`，不重复写 `execution.executed`

截图：[06b-execution-idempotent.png](screenshots/final-demo/06b-execution-idempotent.png)

### 4.8 审计链（升序）

| 时间（UTC） | 动作 | 结果 | 身份 | 关键信息 |
|---|---|---|---|---|
| 19:38:37.479 | `analysis.requested` | success | demo-analyst | 创建分析 |
| 19:38:37.488 | `analysis.claimed` | success | system | worker 领取，attempt 1 |
| 19:38:37.737 | `analysis.llm_fallback` | degraded | demo-analyst | `not_configured` |
| 19:38:37.782 | `recommendation.proposed` | success | demo-analyst | 生成建议 |
| 19:38:37.782 | `recommendation.pending_approval` | success | demo-analyst | 等待人工审批 |
| 19:38:37.796 | `analysis.finished` | success | demo-analyst | 分析完成 |
| 19:38:38.993 | `request.approve.denied` | denied | demo-analyst | `forbidden` |
| 19:38:39.013 | `request.execute.denied` | denied | demo-approver | `not_approved` |
| 19:38:39.494 | `recommendation.approve` | success | demo-approver | 状态 approved |
| 19:38:39.588 | `request.approve.denied` | denied | demo-approver | `invalid_transition` |
| 19:38:39.639 | `request.execute.denied` | denied | demo-analyst | `forbidden` |
| 19:38:39.728 | `execution.started` | success | demo-approver | simulation=true |
| 19:38:39.728 | `execution.executed` | success | demo-approver | before/after 预算，external_calls=0 |
| 19:38:39.996 | `execution.replayed` | replayed | demo-approver | 同 execution_id |

审计页截图：[08-audit.png](screenshots/final-demo/08-audit.png)

### 4.9 运行时长

- 分析请求 → `analysis.finished`：约 **0.317 秒**（19:38:37.479 → 19:38:37.796 UTC）
- 建议生成 → 人工批准：约 **1.71 秒**（19:38:37.782 → 19:38:39.494 UTC）
- 批准 → 模拟执行：约 **0.23 秒**（19:38:39.494 → 19:38:39.728 UTC）
- 完整业务闭环（创建分析 → 执行）：约 **2.25 秒**（不含人工阅读/操作时间）
- 本轮 Playwright 场景 A 证据采集窗口：`19:47:42.045Z` → `19:47:57.260Z`，约 15.2 秒（含页面导航、截图、窗口切换和竞态探测）

## 5. 场景 B：数据不完整

- 数据库：独立临时库 `b.db`，API B `127.0.0.1:8021`，Web B `127.0.0.1:5191`
- 构造方式：删除 `2026-10-30/us-store` 的 3 条每日经营记录（`THERMO-02`、`TRAVEL-BAG-01`、`USB-HUB-03`），不改种子逻辑
- 分析 ID：`6da2385b-8f5b-490c-bd96-00ee7a16aacd`
- 数据质量：`expected_records=360`，`observed_records=357`，`missing_records=3`，`complete=false`
- 问题文案：`当前与上一周期缺少 3 条每日经营记录；不将缺失值当成经营零值`
- 最终状态：`insufficient_data`
- 工作流步骤停在 `receive_request → get_sales_metrics → calculate_profit → analyze_profit_drivers → verify_evidence → wait_for_approval`；没有进入知识检索、LLM、建议生成
- LLM：`status=not_requested`，`reason=insufficient_data`
- 建议：`recommendation_id=null`；页面无“查看经营建议”“批准建议”按钮
- 审计：`analysis.requested` → `analysis.claimed` → `analysis.finished(insufficient_data)`
- 无任何执行记录

截图：[10-scenario-b-overview.png](screenshots/final-demo/10-scenario-b-overview.png)、[10-scenario-b-insufficient-data.png](screenshots/final-demo/10-scenario-b-insufficient-data.png)

## 6. 场景 C：Worker 崩溃与租约恢复

### 6.1 设置

- 数据库：独立临时库 `c.db`
- API C：`127.0.0.1:8022`，`PROFITOPS_ANALYSIS_WORKER_ENABLED=false`，`PROFITOPS_ANALYSIS_LEASE_SECONDS=5`
- 任务：`scenario-c-crash-recovery`，预置为 `queued`，`max_attempts=3`。该行由验收脚本**直接写入数据库**，不经 `POST /api/analyses`；否则 API 的 BackgroundTasks 会先处理任务（见 P1），无法保证由 harness 领取。
- Worker：临时 harness，使用项目真实的 `requeue_expired` / `claim_next_job` / `process_claimed_job`
- 崩溃注入：Worker 1 领取任务后、进入 `process_claimed_job` 前强制终止（`TerminateProcess`），制造“已持有租约但进程消失”的窗口

### 6.2 实际过程

| 阶段 | Worker PID（自报） | 日志/状态 | 数据库 |
|---|---:|---|---|
| 领取并崩溃 | 14848 | `WORKER_CLAIMED scenario-c-crash-recovery`、`WORKER_SLEEPING ... seconds=8.0`，随后进程被终止 | `status=running`，`attempts=1/3`，`worker_id=acceptance-worker:14848`，`lease_until=19:44:56.541009` |
| 租约过期 | — | 无心跳续租 | 仍为 running，直到恢复扫描处理 |
| 新 Worker 恢复 | 23632 | `WORKER_REQUEUED 1`、`WORKER_CLAIMED scenario-c-crash-recovery`、`WORKER_DONE scenario-c-crash-recovery` | `attempts=2/3`，最终 `waiting_approval` |
| 完成 | — | 任务结束 | `worker_id=null`，`lease_until=null`，`error=null` |

> 表中 PID 为 harness `os.getpid()` 自报值；orchestrator 的 `Popen` PID 分别为 17796 和 30276。崩溃注入方式是对本轮启动的 harness 进程执行 `TerminateProcess`，没有触碰用户已有进程。

最终状态：

```json
{
  "id": "scenario-c-crash-recovery",
  "status": "waiting_approval",
  "attempts": 2,
  "max_attempts": 3,
  "recommendation_id": "24ed284e-622a-478e-86da-7a19c7e534a5",
  "steps": ["receive_request","get_sales_metrics","calculate_profit","analyze_profit_drivers","verify_evidence","retrieve_knowledge","llm_root_cause_analysis","verify_llm_evidence","create_recommendation","wait_for_approval"]
}
```

- 建议数量：**1**
- 执行记录数量：**0**
- 没有重复建议、没有重复执行
- 审计：`analysis.claimed` → `analysis.recovered(degraded)` → `analysis.claimed` → `analysis.llm_fallback(degraded)` → `analysis.finished(success)`
- 恢复后 LLM 仍为 `deterministic_fallback` / `not_configured`，没有因恢复而调用外部模型

### 6.3 场景 C 边界

- 崩溃注入点在“已 claim、尚未进入 LangGraph”阶段，不是 LangGraph 节点执行中途；租约/重试/恢复路径是真实代码，但未覆盖节点内部崩溃的中间状态。
- 原始验收时没有独立 worker CLI，临时 harness 是验收代码、不进入仓库；修复后已新增 CLI，详见第 15 节。崩溃时点的强制终止仍由 harness 注入。

## 7. Agent 运维看板

页面：审批人身份下的“Agent 运维”。截图：[07-ops.png](screenshots/final-demo/07-ops.png)

### 7.1 实际数据（24h 窗口）

| 指标 | 值 | 说明 |
|---|---:|---|
| 任务总数 | 2 | 1 个已执行分析 + 1 个 fixture 失败分析 |
| 失败任务 | 1 | fixture `e2e-failed-ecea4cc134d6`，错误 `llm_tool_execution_failed` |
| 重试次数 | 2 | 按 attempts 聚合 |
| 死信 | 0 | 无 dead_letter |
| LLM 运行 | 1 | 成功分析中的 deterministic fallback |
| 模型调用 | 0 | LLM disabled，无 Provider 请求 |
| Provider 请求 | 0 | 同上 |
| 工具调用 / 失败 | 0 / 0 | 无 native tool loop |
| 原生工具运行 | 0 | 同上 |
| Token | 无数据 | 无模型调用，不按 0 伪造 |
| 估算成本 | `no_data` | `amount_usd=null`，没有可定价调用 |
| 待审批建议 | 0 | 已执行 |
| 已执行建议 / 执行记录 | 1 / 1 | 仅场景 A |
| 最近失败任务 | 1 | 可点击“查看 Trace” |

**真实性说明**：

- 成本显示“暂无数据”是正确行为，不是页面缺失。因为本轮 `LLM_PROVIDER=disabled`、`LLM_API_KEY` 为空，`llm.runs=1` 是 deterministic fallback，`model_calls=0`、`provider_requests=0`，没有 Usage，也没有价格。
- 失败任务 `e2e-failed-ecea4cc134d6` 是 `scripts/e2e_fixtures.py` 预置的运维测试 fixture，不是本轮真实 LLM 失败，也不能当作真实 Provider 错误率。
- 页面底部的 `source.note` 明确说明数据来自持久化表，不把进程内 Prometheus 指标伪装成历史。

### 7.2 24h / 72h / 168h 过滤

| 窗口 | HTTP | `window.hours` | 起点（UTC） | 终点（UTC） | 任务总数 | 失败 | LLM 运行 | 成本状态 |
|---:|---:|---:|---|---|---:|---:|---:|---|
| 24h | 200 | 24 | 2026-10-31T19:47:50Z | 2026-11-01T19:47:50Z | 2 | 1 | 1 | no_data |
| 72h | 200 | 72 | 2026-10-29T19:47:50Z | 2026-11-01T19:47:50Z | 2 | 1 | 1 | no_data |
| 168h | 200 | 168 | 2026-10-25T19:47:50Z | 2026-11-01T19:47:50Z | 2 | 1 | 1 | no_data |

三个窗口计数相同，是因为本轮所有记录都落在最近 24 小时内；窗口起点/终点确实不同。页面正常切换时选择器和面板标签一致：`72/72H`、`168/168H`、`24/24H`。

### 7.3 快速切换竞态（P2，原始缺陷，已修复）

用 Playwright 对 `window_hours=168` 响应人为延迟 1.8 秒，然后快速从 168 切回 24：

- 选择器最终值：`24`
- 面板窗口标签：`168H`
- 结论：**旧响应覆盖了当前选择**，`rapid_switch.stale_overwrite=true`
- 原因：`frontend/src/App.tsx` 的 `loadOps` 没有请求序号/取消机制；每次 `opsHours` 变化都会发起请求，最后返回的响应直接 `setOps`
- 影响：在慢网络或快速点击时，用户可能看到与选择器不一致的统计窗口
- 证据截图：[09-ops-stale-window.png](screenshots/final-demo/09-ops-stale-window.png)
- 修复状态：已在 `frontend/src/App.tsx` 的 `loadOps` 加入 `opsSequence` 请求序号；旧窗口响应在 `setOps`/错误/loading 前被丢弃。Vitest 新增延迟 168h 响应后切回 24h 的回归，见第 15 节。

### 7.4 任务状态明细（P2，原始缺陷，已修复）

- `jobs.total=2`
- `jobs.by_status` 只包含 `queued/running/waiting_approval/completed/insufficient_data/failed`，合计为 1（失败任务）
- 已执行分析的 `Analysis.status=executed` 不在 `JOB_STATUSES` 中，因此“任务队列”明细看不到这 1 个已执行任务
- 影响：任务总数与状态明细不一致；“成功任务”没有单独计数
- 代码位置：`backend/app/ops.py` 的 `JOB_STATUSES`
- 修复状态：`JOB_STATUSES` 现包含 queued/running/waiting_approval/completed/insufficient_data/failed/approved/rejected/executed/execution_failed，并纳入观察到的未知状态；`by_status` 之和恒等于 `jobs.total`。运行时复验见第 15 节。

### 7.5 Trace 跳转

- 最近失败任务表格有 1 个“查看 Trace”按钮
- 点击后跳转到 Agent 工作台，标题 `Profit Agent`，状态显示“分析失败”
- 结论：PASS

## 8. 安全与权限验证

| 检查 | 请求 | 结果 | 结论 |
|---|---|---|---|
| 未认证访问受保护 API | `GET /api/analyses` 无 token | `401` | PASS |
| 未认证访问运维 | `GET /api/ops/summary` 无 token | `401` | PASS |
| 分析师访问运维 | 分析师 token | `403` | PASS |
| 审批人访问运维 | 审批人 token | `200` | PASS |
| 分析师批准 | 分析师 token + 伪造 `X-Role: approver` | `403` | PASS，服务端不信任角色头 |
| 未批准先执行 | 审批人 token，待审批建议 | `409 not_approved` | PASS |
| 重复批准 | 审批人 token，已批准建议 | `409 invalid_transition` | PASS |
| 分析师执行 | 分析师 token | `403 forbidden` | PASS |
| 跨租户读取分析 | 租户 B token 读取 demo 分析 | `404` | PASS |
| 跨租户读取建议 | 租户 B token 读取 demo 建议 | `404` | PASS |
| 跨租户运维 | 租户 B token 访问 ops | `403` | PASS |
| demo 读取租户 B 分析 | demo token | `404` | PASS |
| 分析师 UI 运维页 | 分析师身份点击 Agent 运维 | 显示“需要审批负责人权限”，无 KPI | PASS |
| 模拟执行 | 执行响应 | `simulation=true`、`external_calls=0` | PASS |

分析师运维页截图：[11-analyst-ops-denied.png](screenshots/final-demo/11-analyst-ops-denied.png)

## 9. 浏览器与接口运行证据

- Playwright 场景 A 证据：`consoleErrors=[]`、`pageErrors=[]`
- Playwright 场景 B 证据：`consoleErrors=[]`、`pageErrors=[]`
- 分析师运维拒绝页：`consoleErrors=[]`、`pageErrors=[]`
- 场景 A 关键 API：`/api/dashboard/summary`、`/api/analyses`、`/api/analyses/{id}`、`/api/recommendations/{id}`、`/api/recommendations/{id}/approve`、`/api/recommendations/{id}/execute`、`/api/audit-logs`、`/api/ops/summary`
- 场景 B 关键 API：`/api/analyses`、`/api/analyses/{id}`、`/api/audit-logs?object_id=...`
- 场景 C 关键 API：`/health`、直接数据库队列状态、Worker harness 日志
- 本机临时证据文件（未提交）：
  - `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/evidence-a.json`
  - `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/evidence-b.json`
  - `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/evidence-c.json`
  - `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/evidence-security-ui.json`
  - `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/pytest-final.log`
  - `C:/Users/lz/AppData/Local/Temp/profitops-final-demo/smoke-final.log`

## 10. 截图清单

8 张主截图（全部来自本轮真实启动的页面）：

1. [01-overview.png](screenshots/final-demo/01-overview.png) — 经营总览与异常横幅
2. [02-agent-workflow.png](screenshots/final-demo/02-agent-workflow.png) — LangGraph 工作流步骤
3. [03-agent-result.png](screenshots/final-demo/03-agent-result.png) — Agent 结论与证据
4. [04-knowledge-citations.png](screenshots/final-demo/04-knowledge-citations.png) — 知识检索片段/chunk
5. [05-approval.png](screenshots/final-demo/05-approval.png) — 待审批页面（审批前拍摄）
6. [06-execution.png](screenshots/final-demo/06-execution.png) — 模拟执行结果
7. [07-ops.png](screenshots/final-demo/07-ops.png) — Agent 运维与成本看板
8. [08-audit.png](screenshots/final-demo/08-audit.png) — 审计日志

补充证据截图：

- [06b-execution-idempotent.png](screenshots/final-demo/06b-execution-idempotent.png) — 幂等重放
- [09-ops-stale-window.png](screenshots/final-demo/09-ops-stale-window.png) — 快速切换竞态（P2）
- [10-scenario-b-overview.png](screenshots/final-demo/10-scenario-b-overview.png) — 场景 B 总览
- [10-scenario-b-insufficient-data.png](screenshots/final-demo/10-scenario-b-insufficient-data.png) — 场景 B 数据不足
- [11-analyst-ops-denied.png](screenshots/final-demo/11-analyst-ops-denied.png) — 分析师运维拒绝页
- [smoke-overview.png](screenshots/final-demo/smoke-overview.png) — 首次浏览器冒烟

## 11. 回归验证

> 以下为原始验收时的结果；P1/P2 修复后的复验结果见第 15 节。

| 命令 | 结果 | 说明 |
|---|---|---|
| `python -m pytest backend/tests -q` | **260 passed, 6 skipped, 1 warning in 173.10s** | 6 个跳过均为未设置 `PROFITOPS_TEST_POSTGRES_URL` 的 PostgreSQL 测试 |
| `npm --prefix frontend test` | **16 passed (1 file) in 16.43s** | Vitest |
| `npm --prefix frontend run build` | **成功** | 2252 modules，`tsc -b && vite build` 通过 |
| `python scripts/smoke.py --base-url http://127.0.0.1:8010` | **15/15 passed** | 独立 `d.db` 启动 API，跑完停止；覆盖健康、种子、异常、审批、幂等、审计、知识库 |
| 本轮 Playwright 浏览器验收 | **通过** | 场景 A/B/权限页，无 console/page error |
| 完整 Playwright E2E 套件 | **未运行** | 本轮用独立验收脚本覆盖真实浏览器路径；未用 CI 代替浏览器验证 |

## 12. 已知缺陷与限制

### P1：独立 Worker 模式未真正生效（原始缺陷，已修复）

- 现象：设置 `PROFITOPS_ANALYSIS_WORKER_ENABLED=false` 后，`POST /api/analyses` 仍通过 FastAPI `BackgroundTasks` 调用 `process_analysis_job`。
- 实测：在独立 `e.db` 上关闭 in-process Worker，提交分析后状态仍从 `queued → running → waiting_approval`，`attempts=1`，并生成建议。
- 代码位置：`backend/app/main.py:419`（创建分析）和 `backend/app/main.py:495`（重试）无条件 `background_tasks.add_task(...)`。
- 影响：无法仅靠该开关实现“API 不执行任务、独立 Worker 执行任务”的部署模式；多副本/独立 Worker 的运维承诺不成立。
- 附加问题：仓库没有独立 worker CLI；`docs/RUNBOOK.md` 要求关闭开关后另行运行 Worker，但没有可直接执行的入口。本轮场景 C 使用临时 harness。
- 本轮不修改代码；默认 in-process Worker 模式的业务闭环不受影响。
- 修复状态：`create_app` 新增 `inline_jobs`，默认跟随 `analysis_worker_enabled`；关闭 worker 时 POST/retry 不再注册 BackgroundTasks。`backend/app/worker.py` 新增 `python -m app.worker`（支持 `--once`、`--poll-seconds`）。运行时复验见第 15 节。

### P2：运维窗口快速切换旧响应覆盖（原始缺陷，已修复）

- 见 7.3；`frontend/src/App.tsx` 的 `loadOps` 缺少请求序号/取消。
- 演示时避免快速连续切换窗口；生产修复建议为 `loadOps` 增加 sequence guard 或 AbortController。

### P2：运维任务状态明细缺少 executed（原始缺陷，已修复）

- 见 7.4；`backend/app/ops.py` 的 `JOB_STATUSES` 未包含 `executed/approved/executing/rejected/execution_failed`。
- 影响：任务总数与状态明细不一致，成功任务不可见；不丢数据，执行记录/建议状态仍可查。

### P3：幂等重放追加重复执行步骤

- 每次 `POST /execute` 重放都会向 `analysis.steps` 追加 `get_approval_status`、`simulate_execute_approved_action`、`save_execution_result`。
- 执行记录仍只有一条，审计写 `execution.replayed`；属于轨迹噪声，不是重复副作用。
- 影响：多次演示重放后工作流时间线会变长，讲解时需说明“这是重放轨迹，不是重复执行”。

### P3：场景 C 崩溃注入点

- 崩溃发生在 claim 之后、`process_claimed_job` 之前；租约、requeue、attempts、恢复审计均为真实代码，但未覆盖 LangGraph 节点内部崩溃的中间状态。
- 若评审追问“节点执行到一半崩溃”，应回答：本轮未注入该窗口；恢复语义依赖同一套 lease/requeue，但未做该粒度的现场演示。

### P3：fixture 失败任务字段不完全自洽

- `e2e-failed-ecea4cc134d6` 是测试 fixture，`attempts=3/3` 但 `dead_letter=false`，`error_code=llm_tool_execution_failed`。
- 它不是本轮真实 Provider 失败；运维页展示它是为了验证“最近失败任务/Trace 跳转”，不能用于计算真实失败率或模型可靠性。

### 其他未验证/未覆盖

- **真实 DeepSeek**：本轮 disabled，`model_calls=0`；此前真实模型评测是独立报告，不能混入本轮结论。
- **BGE-M3 真实检索**：既有 BLOCKED 状态未变；本轮使用 hashing embedding（`provider=hashing`、`model=hashing-v1`、`remote=false`）。
- **PostgreSQL/Docker**：原始验收使用 SQLite、6 个 PostgreSQL 测试跳过；修复复验已在隔离 `postgres:16-alpine` 容器上跑通，见第 15 节；云端单机 Compose 已在本机隔离栈完成十项验收，见第 16 节。真实云服务器和多副本仍未验证。
- **完整 Playwright E2E 套件**：未运行；本轮自定义验收脚本覆盖了真实页面主路径。
- **生产部署/公网**：未部署；本轮只完成云端单机 Compose 准备与本机隔离验收，没有云服务器凭据，不能声明云端部署完成。

## 13. 冻结标准判断

### 最终演示冻结：满足

必须同时满足以下条件，本轮均满足：

- 默认 in-process Worker 模式下，业务闭环可在浏览器复现：异常 → Agent → 证据 → 审批 → 模拟执行 → 审计
- 无 P0；审批、执行、权限、租户隔离、幂等均有真实证据
- 所有执行 `simulation=true`、`external_calls=0`
- 8 张主截图齐全，且来自真实页面
- 后端/前端/构建/smoke 回归通过
- 已知 P2 不影响主路径；演示时避免快速切换运维窗口

### 生产上线冻结：未满足

- P1/P2 已修复并复验：独立 worker CLI、API 内联任务开关、运维窗口请求序号、完整任务状态明细
- 未在本轮验证真实 LLM、BGE-M3 和多副本；PostgreSQL 单容器已复验
- 因此只能声明“最终演示冻结”，不能声明“生产就绪”

## 14. 最终交付结论

1. **端到端业务闭环是否真正成功**：是。场景 A 在真实 FastAPI + Vite + Playwright 下完成，执行记录唯一且为模拟。
2. **各项 PASS/FAIL**：见第 0 节与第 11 节；主业务、权限、幂等、审计、截图、回归 PASS；P1/P2 已修复并复验（第 15 节）；PostgreSQL 单容器已复验；真实 LLM/BGE-M3/多副本为 SKIP/BLOCKED。
3. **实际前后端运行证据**：API A/B/C 与 Web A/B 的 PID/端口/health、Playwright 无 console/page error、API 状态码、审计链、Worker 日志，见第 1、4、6、9 节；修复复验证据见第 15 节。
4. **8 张截图位置**：见第 10 节。
5. **演示复现步骤**：见 [RUNBOOK.md](RUNBOOK.md) 与 [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md)。
6. **是否满足最终冻结标准**：最终演示冻结满足；生产上线冻结未满足，原因见第 13 节。

## 15. 修复复验（2026-11-02）

### 15.1 修复内容

| 缺陷 | 修复 | 文件 |
|---|---|---|
| P1：关闭 worker 后 API 仍执行任务 | `create_app(..., inline_jobs=None)` 默认跟随 `analysis_worker_enabled`；POST `/api/analyses` 和 retry 仅在 `inline_jobs` 为真时注册 BackgroundTasks | `backend/app/main.py` |
| P1：无独立 worker CLI | 新增 `python -m app.worker`，支持 `--once`、`--poll-seconds`；启动时初始化数据库、回收过期租约，复用同一套 claim/lease/retry/finalize 规则 | `backend/app/worker.py` |
| P2：运维任务状态明细缺 executed | `JOB_STATUSES` 补齐 10 个分析状态，并纳入观察到的未知状态，保证 `sum(by_status) == total` | `backend/app/ops.py` |
| P2：运维窗口快速切换旧响应覆盖 | `loadOps` 增加 `opsSequence` 请求序号，过期响应的数据、错误和 loading 均被丢弃 | `frontend/src/App.tsx` |
| 契约与前端类型 | `JobStatus`、`OpsSummary.jobs.by_status` 收紧；CONTRACT/RUNBOOK 同步独立 worker 与状态口径 | `frontend/src/types.ts`、`docs/CONTRACT.md`、`docs/RUNBOOK.md` |

### 15.2 自动化验证

| 命令 | 结果 | 说明 |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest backend/tests -q` | **263 passed, 6 skipped in 114.23s** | 6 个跳过均为未设置 `PROFITOPS_TEST_POSTGRES_URL` 的 PostgreSQL 测试；使用项目 venv（Python 3.12.8） |
| PostgreSQL 专项（隔离容器） | **50 passed in 15.23s** | `postgres:16-alpine`，端口 55433，含 6 个 PostgreSQL 专项（migration 2、幂等 1、知识库 3）；容器用后删除 |
| `npm --prefix frontend test -- --run` | **17 passed (1 file) in 11.73s** | 新增运维延迟响应回归 |
| `npm --prefix frontend run build` | **成功** | `tsc -b && vite build`，2252 modules |
| `python scripts/smoke.py --base-url http://127.0.0.1:8030` | **15/15 passed** | 见 15.3，API 关闭内联 worker，由独立 worker 消费 |

新增测试：

- `test_disabled_worker_leaves_post_and_retry_queued`：关闭 worker 后 POST 与 retry 均保持 queued、无步骤、无建议。
- `test_worker_cli_once_claims_and_finalizes_one_job`：`python -m app.worker --once` 初始化独立 SQLite、领取一个任务、清理租约并写入终态。
- `test_ops_by_status_includes_post_approval_states`：approved/rejected/executed/execution_failed 均可见且总和等于 total。
- 前端 `ignores a late ops response for a window the operator already left`：168h 响应延迟 150ms，切回 24h 后旧响应不能覆盖面板。

### 15.3 隔离运行复验

使用独立临时数据库 `C:/Users/lz/AppData/Local/Temp/profitops-fix-verify-20261102/verify.db`，未读写正式 `data/profitops.db`，未调用真实模型。

1. 以 `PROFITOPS_ANALYSIS_WORKER_ENABLED=false` 启动 API（`127.0.0.1:8030`，`llm.configured=false`）。
2. `POST /api/analyses` 返回 202/queued；等待 1.5 秒后仍为 `queued`、`steps=0`、`recommendation_id=null`。
3. 运行 `python -m app.worker --once`，输出 `processed 1 job(s)`；同一分析变为 `waiting_approval`、`attempts=1`，生成 1 条建议。
4. 启动独立 worker 轮询（0.2s）后运行 smoke：**15 passed / 0 failed**。
5. 运维汇总：`jobs.total=2`，`by_status` 之和为 2，其中 `executed=1`；`executions.total=1`。
6. 复验结束后只停止本轮启动的 API/worker 进程，未触碰其他服务。

### 15.4 仍未验证

- 真实 DeepSeek：本轮 `LLM_PROVIDER=disabled` / 空 Key，未调用、未计费；此前真实模型评测是独立证据，不能并入本轮。
- BGE-M3 真实检索：既有 BLOCKED 未变；本轮使用 hashing embedding。
- 多副本：独立 worker CLI 已可用，但多副本共享限流、指标聚合和并发压测未验证。
- PostgreSQL 已验证的范围是单容器 + 独立 schema；未验证生产级连接池、主从/高可用和跨副本并发。

## 16. 云端单机 Compose 本地验收（2026-11-02）

本节是第三阶段“本地 Compose 部署验收”的补充记录；完整部署说明见 [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md)。本轮没有云服务器凭据，**未实际部署公网**，以下结果仅来自本机隔离栈。

### 16.1 环境

| 项目 | 值 |
|---|---|
| Compose 项目 | `profitops-cloud-verify`（独立临时栈，未使用正式 `data/profitops.db`） |
| Docker / Compose | 29.5.3 / 5.1.4 |
| 服务 | `db`（PostgreSQL 16 Alpine）、`init`（一次性 Alembic + 种子）、`backend`（FastAPI，关闭内联 worker）、`worker`（`python -m app.worker`）、`frontend`（Nginx + React） |
| 发布端口 | 仅 `frontend`：`127.0.0.1:8443 → 443`；backend/worker/db 无宿主机发布端口 |
| LLM | `LLM_PROVIDER=disabled`、`LLM_API_KEY` 为空；未调用付费模型 |
| 持久化 | 命名卷 `profitops-cloud-verify_profitops_pg`；未执行 `down -v` |

首次 `init` 日志确认：

```text
Running upgrade  -> 0001_baseline, Baseline schema for a fresh ProfitOps AI database.
Running upgrade 0001_baseline -> 0002_tenant_scoped_idempotency, ...
{'seeded': True, 'version': 'identity-v1', 'users': 2, ...}
{'seeded': True, 'version': 'demo-60d-v1', 'days': 60, ...}
{'seeded': True, 'version': 'knowledge-v1', 'documents': 6, ...}
```

重复执行 `init` 返回 `seeded: False`，未删除既有审批、执行或审计数据。API 与 worker 在 `init` 成功后启动；全新 SQLite 库上并发 `initialize_database` 的 `table users already exists` 竞态已通过一次性 `init` 的依赖顺序规避。

### 16.2 十项验收

| # | 验收项 | 结果 | 证据 |
|---:|---|---|---|
| 1 | 创建 queued 任务 | **PASS** | `POST /api/analyses` 202，`status=queued` |
| 2 | FastAPI 不执行任务 | **PASS** | worker 停止 3s 后仍 `queued`、`steps=0`、无建议 |
| 3 | 独立 worker 完成任务 | **PASS** | `start worker` 后 `waiting_approval`、`attempts=1` |
| 4 | 重启 API 不影响 worker | **PASS** | `restart backend` 前后 worker `StartedAt` 不变，新任务完成 |
| 5 | 关闭 worker 后任务保留 queued | **PASS** | 新任务保持 `queued` |
| 6 | 恢复 worker 后继续处理 | **PASS** | 同一任务变为 `waiting_approval` |
| 7 | 人工审批、模拟执行、幂等、审计 | **PASS** | 同一 `execution_id` 重放，`simulation=true`、`replayed=true`；审计含 `recommendation.approve`、`execution.executed`、`execution.replayed` |
| 8 | 前端访问运维看板 | **PASS** | Chromium HTTPS + Basic Auth；任务队列、任务总数、模型与工具、Token 与成本、错误与人工环节、已执行建议均可见；`consoleErrors=[]` |
| 9 | 数据库重启后记录保留 | **PASS** | `jobs.total=6`、`executions.total=2` 重启前后一致，已完成分析仍为 `executed` |
| 10 | 仅允许的监听端口 | **PASS** | 仅 `frontend 127.0.0.1:8443->443/tcp`；其余服务无发布端口 |

补充：未带 Basic Auth 请求 `/api/demo` 返回 401；运维聚合 `jobs.by_status` 之和等于 `jobs.total`；验收截图见 [screenshots/final-demo/cloud-compose-ops.png](screenshots/final-demo/cloud-compose-ops.png)。

### 16.3 PASS / FAIL / SKIP / BLOCKED

| 项目 | 状态 | 说明 |
|---|---|---|
| 本地 Compose 单机栈 | **PASS** | 5 个服务实际启动并通过健康检查 |
| 首次 Alembic + 种子 | **PASS** | 0001/0002 与 `app.init` 首次成功；重复执行幂等 |
| 独立 worker 队列 | **PASS** | queued → worker claim → waiting_approval；API 不内联执行 |
| API/worker 分离 | **PASS** | 重启 API 不影响 worker；停止 worker 任务保留 |
| 审批/执行/幂等/审计 | **PASS** | 同键重放同一 execution_id；审计动作完整 |
| 前端运维看板 | **PASS** | HTTPS + Basic + Chromium 实测，无 console/page error |
| 数据库持久化 | **PASS** | 数据库重启后记录数与状态保持 |
| 端口最小暴露 | **PASS** | 仅 frontend 443（本机 8443）发布 |
| 真实云服务器部署 | **SKIP / BLOCKED** | 本轮无云服务器凭据，未执行；只完成准备与本机验收 |
| 真实 DeepSeek | **SKIP** | 按约束禁用，未调用、未计费 |
| BGE-M3 真实检索 | **BLOCKED（沿用既有结论）** | 本轮使用 hashing embedding，不重新评估 |
| 多副本 / Kubernetes / Redis / Celery | **SKIP** | 明确不在本轮范围；当前单机单 API + 单 worker |
| 真实广告/采购/支付副作用 | **PASS（无）** | 所有执行 `simulation=true`、`external_calls=0` |

### 16.4 结论

- 本地隔离 Compose 栈的十项验收全部 PASS，云端单机配置、初始化、备份、排障和访问控制方案已准备完成。
- **未实际部署到云服务器**：没有云服务器凭据，不能声明“云端部署完成”；目标主机上线仍需按 [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) 第 4–6 节执行并重新验收。
- 本阶段完成后停止功能开发；不自动合并 PR。

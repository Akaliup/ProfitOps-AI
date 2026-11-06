<div align="center">

# ProfitOps AI

**跨境电商利润经营决策 Agent** · Cross-border E-commerce Profit Operations Agent

一个工作台就是一条经营账本。销售额上涨、利润下降时，Agent 从账本计算利润变化，定位会计贡献项，检索运营规则，生成待审批建议；人工批准后只执行受控模拟，并留下完整审计记录。

[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-3776AB?logo=python&logoColor=white)](#快速开始)
[![Node.js](https://img.shields.io/badge/Node.js-%E2%89%A520.19-339933?logo=nodedotjs&logoColor=white)](#快速开始)
[![tests](https://img.shields.io/badge/tests-277%20passed-brightgreen)](#已验证的能力)
[![RAG](https://img.shields.io/badge/RAG-42%20queries-6E56CF)](#已验证的能力)
[![MCP](https://img.shields.io/badge/MCP-stdio-6E56CF)](#mcp-工具面)
[![simulation](https://img.shields.io/badge/execution-simulation%20only-orange)](#关键设计决策面试会被追问的)

`MVP 可演示版本` · 默认离线 · 所有执行均为 simulation

</div>

---

## 为什么做这个

跨境经营的日常问题高度重复：**销售额涨了，利润为什么反而跌了？广告、采购、履约、退款各占多少？下一步该动哪个 SKU？** 答案散在账本、平台规则和人的经验里。通用聊天机器人接不住，因为它既算不准利润，也不敢让 AI 直接改预算。

ProfitOps 把三件事拆开：

- **数字来自账本，不来自模型**——利润、环比、贡献桥和动作参数由确定性代码按整数美分计算；LLM 只负责解释与策略文案。
- **规则必须带引用**——RAG 返回稳定的 `chunk_id`，未知引用让整次 LLM 输出作废；检索不到就明确 `no_answer`，不拿低分片段凑答案。
- **写操作必须人工确认**——建议先进入 `pending_approval`，审批依赖条件 UPDATE、事务和唯一约束；执行只写模拟记录，不调用真实广告、采购或支付系统。

---

## 界面

| 经营总览 | 运营知识库 |
|---|---|
| ![经营总览](docs/screenshots/overview.png) | ![运营知识库](docs/screenshots/knowledge.png) |

| 人工审批 | Agent 执行轨迹 |
|---|---|
| ![人工审批](docs/screenshots/approval.png) | ![Agent 执行轨迹](docs/screenshots/agent-trace.png) |

<details>
<summary>展开其余 3 张截图</summary>

| | |
|---|---|
| ![模拟执行](docs/screenshots/execution.png) | ![无可靠答案](docs/screenshots/knowledge-no-answer.png) |
| ![移动端布局](docs/screenshots/mobile.png) | |

</details>

---

## 快速开始

```bash
git clone https://github.com/Akaliup/ProfitOps-AI.git
cd ProfitOps-AI
python scripts/dev.py
```

打开 <http://127.0.0.1:5180>；API 文档在 <http://127.0.0.1:8010/docs>，健康检查在 <http://127.0.0.1:8010/health>。

> 默认离线：不需要 API Key、Docker 或外部数据库。脚本会创建 `.venv`、按锁定版本安装依赖并启动前后端；后端首次启动会自动初始化 2 店铺 × 3 SKU × 60 天演示数据。
> 依赖已安装时用 `python scripts/dev.py --skip-install`；端口被占用时用 `--api-port 8020 --web-port 5190`。

其他命令：

| 命令 | 作用 |
|---|---|
| `python scripts/dev.py` | 一键安装依赖并启动前后端（后端首次启动自动初始化演示数据） |
| `python -m pytest backend/tests -q` | 后端单元、集成、工作流、并发、知识库与 MCP 测试 |
| `npm --prefix frontend test` | 前端交互测试（Vitest + RTL） |
| `npm --prefix frontend run build` | TypeScript 检查与生产构建 |
| `python backend/evals/rag.py` | RAG 检索评测，一次对比 weighted 与 hybrid_rrf |
| `python backend/evals/replay.py` | LLM 录制响应回放，离线验证解析与证据链路 |
| `python scripts/reindex_knowledge.py --dry-run` | 预览知识库重嵌入；去掉 `--dry-run` 执行，运行前备份数据库 |
| `python scripts/mcp_check.py` | MCP stdio 真实子进程检查 |
| `python scripts/smoke.py` | HTTP 冒烟（需服务已启动） |
| `python scripts/browser_test.py` | Playwright 真实浏览器验收 |

### 可选：接入真实 LLM

不配置任何变量也能完整运行；没有 Key 时自动降级为确定性规则。要启用真实 DeepSeek，在项目根目录 `.env`（已被 Git 忽略）或后端进程环境中设置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | `deepseek` 或 `disabled` |
| `LLM_MODEL` | `deepseek-flash` | DeepSeek-V4.1-Flash-1017 的 API 模型 ID |
| `LLM_API_KEY` | 空 | 只放在本地 `.env` 或进程环境，不提交、不打印 |
| `LLM_FALLBACK_POLICY` | `rules` | `rules`：失败降级为规则；`fail`：分析失败并记录 `llm_failed` |
| `KNOWLEDGE_EMBEDDING_PROVIDER` | `hashing` | `hashing`（离线）或 `openai`（任意 OpenAI 兼容 `/embeddings`） |
| `KNOWLEDGE_MIN_SCORE` | `0.15` | 拒答阈值；换 embedding 必须重新校准 |

切换 embedding 模型后，旧库可用 `python scripts/reindex_knowledge.py` 就地重嵌入：先 `--dry-run` 预览，运行前备份数据库；它保留文档/chunk ID、标题和审计记录，只替换向量与 `embedding_model`。

完整变量见 [`.env.example`](.env.example) 与 [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md)。

---

## 演示脚本（约 3 分钟，一镜到底）

| # | 操作 | 屏幕上出现什么 | 讲什么 |
|---|---|---|---|
| 1 | `python scripts/dev.py` | 打印工作台和 API 文档地址；后端首次启动自动初始化 60 天数据 | 一条命令起全栈，SQLite 落地，运行时不依赖外部服务 |
| 2 | 打开经营总览 | 销售额上涨、净利润下降，广告支出是最大负向贡献项 | 数字来自账本和整数美分计算，不是模型生成 |
| 3 | 点「启动 Profit Agent」 | 读取数据 → 利润计算 → 异常归因 → 证据核验 → 检索知识库 → LLM/规则 → 创建建议 | LangGraph 节点与 RAG 都在同一条链路里，失败会显式降级 |
| 4 | 展开「Agent 执行轨迹」 | 轮次、工具名、参数摘要、成功/失败、错误码、token、耗时 | 失败调用不会被显示成成功，轨迹可逐轮核对 |
| 5 | 点「查看经营建议」 | 8 项利润贡献、证据 ID、知识片段、LLM 状态与引用标记 | 金额、证据和引用都可追溯；LLM 只解释，不改数字 |
| 6 | 以分析师身份尝试批准 | 页面不提供按钮；直接用 API 伪造 `X-Role` 返回 403 | 角色由服务端 token 决定，不信任前端请求头 |
| 7 | 切换审批负责人 → 批准 → 模拟执行 | `SIMULATION`、预算预览、外部调用 0 次 | 写操作有人工闸门，执行只写数据库预览 |
| 8 | 再点「再次请求 · 验证幂等」 | 返回同一执行 ID，提示命中幂等 | 条件 UPDATE + 唯一约束，重复请求不会产生第二条执行记录 |
| 9 | 打开运营知识库 | 检索「广告预算下调审批」命中片段；检索「如何预订会议室午餐」显示「无可靠答案」 | 规则必须带引用，检索不到就拒答 |
| 10 | 打开审计日志，按请求 ID 过滤 | 分析、批准、开始执行、成功执行、重复回放、被拦截请求 | `request_id` 贯穿全链路，谁在什么时候做了什么一目了然 |

### 端到端演示验证（2026-11-07）

在独立端口（后端 8020 / 前端 5190）、临时 SQLite 库、`LLM_PROVIDER=disabled`、`hashing` embedding 下实际走完上面 10 步：浏览器操作与 API 断言共 29 项全部通过，无浏览器 console/page error。演示未触碰默认 8010/5180 的本地数据，没有调用付费模型，也没有任何真实外部副作用。原始证据（29 项检查、请求 ID、执行 ID 与响应字段）见 [evidence.json](docs/screenshots/demo-20261107/evidence.json)。

| 环节 | 实测结果 |
|---|---|
| 经营总览 | 销售额 `US$45,600.00`（4,560,000 美分）、净利润 `-US$5,703.00`；异常标记与利润桥守恒均为 true |
| Agent 工作流 | 10 个 LangGraph 步骤全部 completed，状态 `waiting_approval`；LLM 未配置时降级为确定性规则且状态可见 |
| 建议与证据 | 8 项证据均带 `evidence.id` 与 `source`；知识库检索命中 5 个可引用片段 |
| 越权拦截 | 分析师界面无「批准建议」按钮；用分析师 token 伪造 `X-Role: approver` 调用 API 返回 `403` |
| 审批与执行 | 审批负责人批准后模拟执行：`simulation=true`、`external_calls=0`，日预算 `US$255.50 → US$217.17`（25,550 → 21,717 美分） |
| 执行幂等 | 再次请求返回同一 `execution_id`，页面提示「重复请求已命中原执行记录」 |
| 知识检索 | 「广告预算下调审批」命中 5 个片段、`no_answer=false`；「如何预订会议室午餐」`no_answer=true` |
| 审计链路 | 按 `request_id` 过滤得到 `analysis.requested` 等事件；全量审计同时包含 `recommendation.approve`、`execution.executed`、`execution.replayed` |

| 经营总览 | Agent 工作流 |
|---|---|
| ![经营总览](docs/screenshots/demo-20261107/01-overview.png) | ![Agent 工作流](docs/screenshots/demo-20261107/02-agent-workflow.png) |

| 待审批建议 | 模拟执行 |
|---|---|
| ![待审批建议](docs/screenshots/demo-20261107/03-approval-pending.png) | ![模拟执行](docs/screenshots/demo-20261107/04-execution.png) |

<details>
<summary>展开其余 4 张截图</summary>

| 幂等回放 | 知识命中 |
|---|---|
| ![幂等回放](docs/screenshots/demo-20261107/05-idempotent.png) | ![知识命中](docs/screenshots/demo-20261107/06-knowledge-hit.png) |

| 知识拒答 | 审计过滤 |
|---|---|
| ![知识拒答](docs/screenshots/demo-20261107/07-knowledge-no-answer.png) | ![审计过滤](docs/screenshots/demo-20261107/08-audit-filtered.png) |

</details>

> 离线 `hashing` 的拒答阈值使用 `KNOWLEDGE_MIN_SCORE=0.15`（本次演示显式设置）。如果本地 `.env` 是按 BGE-M3 校准的（例如 `0.42`），切回 `hashing` 时必须同步下调，否则知识检索会全部拒答。

---

## 架构

```
浏览器 / 工作台（React + Vite）
   │ HTTP + Bearer token
   ▼
FastAPI 控制面（backend/app）
├ 认证与角色：服务端 token → analyst / approver
├ 分析任务：持久化队列 + worker + 重试/租约
├ LangGraph：利润计算 → 证据校验 → RAG → LLM 根因 → 引用校验 → 建议
├ 审批/执行：条件 UPDATE + 事务 + 唯一约束 + 审计
├ 知识库：Markdown 切分 → hashing/OpenAI embedding → BM25 + 向量 RRF → no_answer
└ SQLite / PostgreSQL：账本、建议、执行、知识、审计
   │
   ├── DeepSeek Provider（可选；失败降级为规则）
   └── MCP stdio Server（只读 4 工具，复用 Tool Registry）
```

**语言边界的理由**：控制面、计算面和工具面都在 Python 后端，前端只做展示与交互；MCP Server 是独立只读进程，复用同一套 Tool Registry。金额口径、角色校验、审批状态机只有一份实现，避免前后端各算一套。

### 一次分析的完整链路

```
POST /api/analyses
  → worker 领取任务
  → get_sales_metrics（账本）
  → calculate_profit（整数美分）
  → analyze_profit_drivers（贡献桥）
  → verify_evidence（证据 ID）
  → retrieve_knowledge（RAG；无可靠答案也继续）
  → llm_root_cause_analysis（可选；原生工具循环）
  → verify_llm_evidence（evidence_id + chunk_id）
  → create_recommendation（pending_approval）
  → 人工 approve
  → execute（simulation only；幂等）
  → audit_logs（request_id 贯穿）
```

---

## MCP 工具面

`backend/app/mcp_server.py` 通过 stdio 暴露同一套 Tool Registry 的 4 个只读工具；副作用等级由 Tool Registry 声明，MCP 侧只允许 `read` / `pure` 且 `policy=safe` 的工具。

| 工具 | 副作用 | 说明 |
|---|---|---|
| `get_profit_analysis` | `read` | 读取指定周期的可信利润报告、驱动与证据；模型只能提供 `period` / `store_id` |
| `get_sku_breakdown` | `read` | SKU 利润与广告变化明细 |
| `search_knowledge` | `read` | 检索知识库，返回与 HTTP/LangGraph 一致的稳定 `chunk_id` |
| `get_approval_status` | `read` | 读取服务端审批状态 |

建议写入、审批和模拟执行**不会**暴露给 MCP 客户端；角色由 `PROFITOPS_MCP_ROLE` 在服务端决定，默认 `analyst`。

```bash
python scripts/mcp_check.py
```

脚本会启动真实 stdio 子进程并逐个调用 4 个工具；手动启动方式见 [docs/MCP.md](docs/MCP.md)。

---

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查（无认证） |
| `POST` | `/api/auth/login` · `GET /api/auth/me` | JWT 登录 / 当前用户 |
| `GET` | `/api/dashboard/summary` | KPI、趋势、利润桥、SKU 明细 |
| `POST` | `/api/analyses` | 创建分析任务（202，可选 `Idempotency-Key`） |
| `GET` | `/api/analyses/{id}` | 分析详情、步骤、LLM 轨迹 |
| `POST` | `/api/analyses/{id}/retry` | 失败任务重新排队 |
| `GET` | `/api/recommendations` | 建议列表 |
| `POST` | `/api/recommendations/{id}/approve` · `/reject` | 审批 / 拒绝（仅 approver） |
| `POST` | `/api/recommendations/{id}/execute` | 幂等模拟执行（必须 `Idempotency-Key`） |
| `GET` | `/api/audit-logs` | 审计日志（可按 `request_id` / `object_id` 过滤） |
| `POST` | `/api/knowledge/search` | RAG 检索 |
| `GET` · `POST` | `/api/knowledge/documents` | 文档列表 / 新增（新增仅 approver） |
| `POST` · `DELETE` | `/api/knowledge/documents/{id}/reindex` · `/api/knowledge/documents/{id}` | 重嵌入 / 停用（仅 approver） |
| `GET` | `/api/tools` | 九个工具及其 JSON Schema |
| `GET` | `/api/ops/summary` | Agent 运维聚合（仅 approver） |

错误格式统一为 `{"error":{"code","message","details"},"request_id":"..."}`；响应头也包含 `X-Request-ID`。`/docs` 提供 OpenAPI 文档。

---

## 目录

```
backend/app/
├── main.py              HTTP 路由、生命周期、任务队列入口
├── metrics.py           利润、环比、贡献桥、证据校验
├── workflow.py          LangGraph 分析工作流
├── services.py          建议、审批、模拟执行、幂等
├── knowledge.py         Markdown 切分、embedding、混合检索、no_answer
├── bm25.py              内存 Okapi BM25
├── tools.py             Tool Registry 与工具策略
├── llm.py               DeepSeek Provider、熔断、重试
├── llm_tools.py         原生 Function Calling 工具循环
├── mcp_server.py        只读 MCP stdio Server
├── models.py            SQLAlchemy 模型
└── audit.py             审计事件
backend/tests/           单元、集成、工作流、并发、知识库、MCP、评测
backend/evals/           LLM Agent 评测样本、RAG/回放评测、Runner 与报告
backend/migrations/      Alembic 基线与增量迁移
frontend/src/            React 工作台、API 类型、交互测试
frontend/e2e/            Playwright 真实浏览器验收
scripts/                 一键启动、迁移、冒烟、浏览器测试、MCP/LLM 验证、知识库重嵌入
docs/                    契约、架构、运行手册、云端部署、知识库、MCP、验证记录、截图
compose.yaml             PostgreSQL + 后端 + 前端
compose.cloud.yaml       云端单机覆盖：独立 worker、一次性 init、HTTPS 入口
AGENTS.md                编码 Agent 工作约定
TASKS.md                 交付与后续任务
```

---

## 已验证的能力

2026-11-03 在 RAG 分支实际执行：

```
python -m pytest backend/tests -q                 277 passed, 6 skipped
python -m pytest backend/tests/test_bm25.py -q    5 passed
python backend/evals/rag.py                       weighted / hybrid_rrf 退出码 0
                                                  hashing: hit@1 97.22%, hit@3 100%, MRR 0.9815,
                                                           no-answer 100%, citation validity 100%
                                                  BGE-M3: hit@1 100%, MRR 1.0000, no-answer 100%,
                                                          coverage@3 50%
                                                  qwen3-embedding:0.6b: hit@1 100%, coverage@3 75%
npm --prefix frontend run build                   通过（Vite 6.4.5）
```

带 * 的是上一轮既有结果，RAG 本轮未重跑：

```
* npm --prefix frontend test                     17 passed（2026-11-02）
* python scripts/mcp_check.py                    MCP stdio 检查通过（2026-11-02）
* python scripts/smoke.py                        15/15 passed（2026-11-02）
* npm --prefix frontend run test:e2e             7 passed（2026-11-02）
```

2026-11-07 在 UI 改版分支实际执行：

```
npm --prefix frontend test                        17 passed
npm --prefix frontend run build                   通过（Vite 6.4.5）
npm --prefix frontend run test:e2e                7 passed
python scripts/smoke.py                           15/15 passed
端到端演示（独立端口 + 临时库 + 离线）             29/29 检查通过，8 张截图
```

hashing 下 `weighted` 与 `hybrid_rrf` 指标相同：hashing 没有排序能力，不参与稠密路投票，两种排序在这套语料上等价。

覆盖：整数美分与利润守恒、证据 ID 可追溯、LLM 失败/降级状态可见、审批与执行幂等、服务端角色校验、RAG 拒答与引用校验、MCP 只读边界、无真实外部副作用。

---

## 关键设计决策（面试会被追问的）

| 决策 | 选择 | 理由 |
|---|---|---|
| 金额 | 整数美分存储与传输 | 浮点会在折扣、退款和贡献桥上累积误差；前端只负责 `/100` 展示 |
| 利润与归因 | 确定性代码计算 | LLM 只解释，不计算；换模型不影响数字 |
| 证据 | `evidence_id` 必须来自本次确定性证据 | 未知引用让整次 LLM 输出作废，不进入审批 |
| RAG | BM25 + 向量 RRF，`no_answer` 看 `confidence` | RRF 是排名量，不能当概率；hashing 没有排序能力，不参与稠密路投票 |
| 审批 | 条件 UPDATE + 事务 + 唯一约束 | 并发下先查后写会双批；约束才是真相 |
| 执行 | 只写模拟记录，幂等键必填 | 不调用真实广告/采购/支付；重复请求返回同一执行 ID |
| 角色 | 服务端 token 决定 | 不信任 `X-Role`；analyst 伪造请求返回 403 |
| LLM 失败 | `llm_success` / `deterministic_fallback` / `llm_failed` / `not_requested` | 不把规则结果伪装成 LLM 输出 |
| 数据不足 | 停在 `insufficient_data` | 每日经营记录缺失或标记不完整时，不生成可执行建议 |
| MCP | 只读 allowlist，4 个工具 | 写建议、审批、执行永不暴露给 MCP 客户端 |

---

## 与普通 LLM Demo 的区别

| 维度 | 普通 LLM Demo | ProfitOps |
|---|---|---|
| 数字 | 模型生成或复述 | 账本 + 整数美分确定性计算 |
| 规则 | 模型自由发挥 | RAG 片段 + `chunk_id` 引用校验，`no_answer` 拒答 |
| 写操作 | 直接执行或只打印 | 人工审批 + 条件更新 + 幂等模拟执行 |
| 失败 | 吞掉或无限重试 | 降级状态、错误码、执行轨迹可见 |
| 审计 | 无 | `request_id` 贯穿分析、审批、执行与回放 |

---

## M2 路线（按优先级）

1. **向量索引与 reranker**：BGE-M3 / qwen3-embedding 已验证，下一步接专用向量索引（如 pgvector）与 reranker，保留 BM25 + RRF 作为可回退排序。
2. **知识库治理**：文档级 ACL、PDF/OCR 入库、版本/生效日期过滤、批量重嵌入。
3. **身份与多租户**：OIDC/SSO、MFA、密钥轮换、租户管理后台；当前演示认证不是生产 IAM。
4. **多副本运行**：共享限流/指标存储、独立 worker 扩缩容、队列与租约压测。
5. **真实模型评测**：扩大真实 DeepSeek 调用样本，加入成本/延迟门禁；自动 CI 仍只用 Fake/回放。
6. **云端部署**：在目标主机复验 `compose.cloud.yaml` 的初始化、备份恢复、TLS 与访问控制。

---

## 开发方式与协作边界

本项目由**本人担任技术负责人**：确定业务口径与利润公式、定义 API/数据库/状态机契约、设计评测门禁与验收标准、审查每次合并；**具体编码实现由 Claude Code 完成**。

仓库根目录的 [`AGENTS.md`](AGENTS.md) 是给编码 Agent 的工作约定：禁止直接推 `master`、契约变更必须同步测试与前端类型、真实 LLM 调用不得进入 CI、未实际验证不得写成通过。取舍很明确：**吞吐来自 AI，判断必须来自本人**。完整边界见 [docs/COLLABORATION.md](docs/COLLABORATION.md)。

---

## 已知限制（别在面试里吹过头）

- 认证是可运行的自建账号 + JWT + 租户隔离，但不是完整生产 IAM：没有 OIDC/SSO、密码重置、MFA、密钥轮换和租户管理后台；限流桶和指标是进程内状态，多副本部署需要共享存储。
- LLM 只负责解释与策略文案；真实 DeepSeek 共调用 22 次，原生 Function Calling 只做了 3 次脚本运行，样本很小，不能据此声明真实调用成功率或工具选择准确率。
- RAG 是 6 篇合成 SOP + 42 条标注查询；hashing 是离线工程方案，不是语义模型；BGE-M3 / qwen3-embedding 已验证，但语料仍小。未实现文档级 ACL、PDF/OCR、reranker 或专用向量数据库；换 embedding 必须重新校准 `KNOWLEDGE_MIN_SCORE`。
- Phase 2D 工具调用评测默认使用脚本化 `FakeToolProvider`，37 次运行只证明生产工具循环和门禁在合成案例上的行为，不是真实模型的工具选择准确率。
- 云端单机 Compose 只在本地隔离栈完成十项验收，未在真实云主机部署；HTTP 冒烟、浏览器 E2E、前端单测和 MCP stdio 检查是上一轮既有结果，RAG 本轮未重跑。
- MCP 只实现了 stdio 只读服务（4 个工具），没有网络 transport、OAuth、多租户隔离和 MCP 侧审计。
- 模拟执行只写数据库预览，不改变任何真实平台；预期影响是明确标注的条件性情景，不构成收益承诺。
- 超时在步骤结束后检查，不能强制中断阻塞中的同步工具。

---

## 延伸阅读

- [docs/CONTRACT.md](docs/CONTRACT.md)：利润口径、API、数据库与状态机契约。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：整体架构与关键设计。
- [docs/RUNBOOK.md](docs/RUNBOOK.md)：本地运行、排障与恢复。
- [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md)：RAG 数据模型、切分、embedding、BM25 + RRF、引用校验与限制。
- [docs/EVALUATION.md](docs/EVALUATION.md)：Agent、原生工具调用与 RAG 评测记录。
- [docs/MCP.md](docs/MCP.md)：MCP 工具、客户端配置与安全边界。
- [docs/CLOUD_DEPLOYMENT.md](docs/CLOUD_DEPLOYMENT.md)：云端单机 Compose、初始化/迁移、备份恢复与访问控制。
- [docs/FINAL_DEMO_ACCEPTANCE.md](docs/FINAL_DEMO_ACCEPTANCE.md)：最终演示验收清单与结果。
- [docs/TEST_REPORT.md](docs/TEST_REPORT.md)：每次交付实际执行的命令与结果。
- [docs/COLLABORATION.md](docs/COLLABORATION.md)：AI 协作边界与本人负责的判断。

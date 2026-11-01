# ProfitOps AI 面试准备（2026-11-02）

本文基于当前仓库和本轮实际验证记录编写。原则：**只讲代码里存在、测试或运行记录里验证过的能力**；设计但未实现的能力明确标注；模拟能力不包装成真实业务系统。旧版 `docs/INTERVIEW.md` 中“BackgroundTasks”“8 条 RAG 查询”等描述已被本文件取代。

---

## A. 项目背景、业务问题与技术选择

### A1. 业务问题

跨境电商团队常见的情况是：销售额在涨，利润却在跌；订单、广告、履约、平台费、退款分散在不同表或系统里，运营能看到“销售额”，但说不清“哪一项吃掉了利润”，更不敢直接改广告预算或采购计划。

ProfitOps AI 把这个问题拆成三步：

1. **算清楚**：把订单与每日经营数据统一成整数美分的利润桥，八项贡献之和必须等于净利润变化。
2. **讲清楚**：用确定性规则定位主要负向贡献项，可选调用 DeepSeek 做根因解释和策略文案；所有结论必须引用已有证据。
3. **控住动作**：建议必须由审批负责人批准，执行只在事务中写模拟记录，不触碰真实广告、采购或支付系统。

它不是一个“让 LLM 直接算利润”的聊天机器人，也不是一个只展示图表的 BI 报表。核心差异是：**数字由确定性代码计算，LLM 只做解释；每个动作有证据、有审批、有幂等、有审计。**

### A2. 技术选择与理由

| 选择 | 理由 | 代价/边界 |
|---|---|---|
| Python 3.11+ / FastAPI | 类型化 API、Pydantic 校验、异步生态；与 SQLAlchemy/LangGraph 集成直接 | 单进程演示；生产需要独立 worker/网关 |
| SQLAlchemy + SQLite/PostgreSQL | SQLite 零依赖可复现；PostgreSQL 提供生产式事务与迁移验证 | 两者 SQL 行为有差异，迁移测试必须分别跑 |
| LangGraph | 有状态、可暂停、可恢复；`wait_for_approval` 是真实业务暂停点 | 图不是分布式执行引擎；任务调度由数据库队列承担 |
| 确定性规则 + 可选 LLM | 利润/证据/金额不能被模型改写；LLM 负责归纳与文案 | LLM 失败必须显式降级，不能伪装成功 |
| 原生 Function Calling | 模型可主动查询利润与 SKU 明细，工具由服务端执行和校验 | 需要允许列表、参数/范围/轮次/预算限制 |
| React + TypeScript + Vite | 真实 API 驱动的工作台；类型与后端契约同步 | 没有接入完整登录页，演示用身份切换 |
| 进程内指标 + 固定容量限流 | 不引入 Prometheus/Redis 依赖，单机可运行 | 多副本必须换共享存储；指标不是持久历史 |
| Alembic | 旧库原地迁移，不删业务表 | 迁移必须同时验证 SQLite 与 PostgreSQL |

---

## B. 30 秒、1 分钟、3 分钟项目介绍

### B1. 30 秒

> ProfitOps AI 是一个跨境电商利润经营决策 Agent。它把订单、广告、履约、平台费等账本统一成整数美分的利润桥，用 LangGraph 组织“读取数据 → 计算利润 → 归因 → 证据校验 → 知识检索 → LLM 解释 → 建议 → 人工审批 → 模拟执行”的流程。数字全部由确定性代码计算，DeepSeek 只做解释，每条结论必须引用已有证据；没有 Key 或调用失败时明确降级。审批和执行有服务端角色校验、条件 UPDATE 和幂等约束，所有执行都是 simulation，并留下可按请求 ID 追踪的审计记录。

### B2. 1 分钟

> 很多跨境团队能看到销售额上涨，却说不清利润为什么下降。这个项目先解决“算清楚”：金额统一为整数美分，利润桥八项贡献必须和净利润变化对平；再用规则引擎定位主要负向贡献项。
>
> 然后解决“讲清楚”：分析流程用 LangGraph 持久化每一步，知识库检索运营 SOP，DeepSeek 只负责根因解释和策略文案。模型可以通过原生 Function Calling 调用三个只读工具，但工具必须通过允许列表、Pydantic 参数、period/store 范围、角色和轮次/预算校验；输出里的 evidence ID 和 knowledge chunk ID 必须属于本次允许集合，否则整次输出作废并降级。
>
> 最后解决“控住动作”：建议必须由审批负责人批准，执行只写模拟记录，不调用真实广告/采购/支付；审批和执行用条件 UPDATE 抢占状态，执行记录有唯一约束和租户级幂等键。任务队列有 lease、心跳、fencing、重试和死信，进程崩溃后过期租约可以恢复。前端有 Agent 工作台、审批中心、知识库、审计和 Agent 运维页。所有结论都能回到代码、测试和运行记录。

### B3. 3 分钟

> **背景（30 秒）**：跨境团队的数据分散在订单、广告、履约、平台费和退款里。销售额上涨时，利润可能被履约或退款吃掉。传统 BI 能画图，但不能解释“哪一项贡献了多少”，也不能安全地执行动作。
>
> **方案（60 秒）**：ProfitOps 用确定性代码算利润：金额是整数美分，利润桥八项贡献之和必须等于净利润变化；退款只扣收入，采购成本已是退货后残值，避免重复扣减。规则引擎按负向贡献排序找主要驱动。分析是数据库中的持久化任务，不是进程内 BackgroundTasks：worker 用条件 UPDATE 抢占，记录 attempts、worker_id、lease_until，执行期间心跳续租；租约过期后旧 worker 不能再写建议或结果，这就是 fencing。LangGraph 把每个节点写入 steps 和 snapshot，审批是真实暂停点；审批后从数据库快照进入独立执行图，不重跑建议。
>
> **LLM 与 RAG（45 秒）**：DeepSeek 只做根因解释和策略文案，不返回金额。模型可调用 `get_profit_analysis`、`get_sku_breakdown`、`search_knowledge` 三个只读工具；服务端校验允许列表、policy/effect、角色、Pydantic 参数、period/store 范围、重复调用和轮次预算。知识库是 6 篇合成 SOP，检索用混合分数，低置信度返回 `no_answer`；模型只能引用本次返回的 chunk_id。输出经过独立证据校验，未知 evidence 或 knowledge 引用会让整次输出作废并降级。
>
> **安全与交付（45 秒）**：认证支持 demo token 和 HS256 JWT，服务端每次请求重新读取用户、角色、租户，不信任 `X-Role`。所有查询按 tenant_id 过滤，跨租户返回 404。审批/执行有 approver 角色、条件 UPDATE、唯一执行记录和租户级幂等键。任务失败按可重试错误指数退避，达到上限进入死信；手动 retry 只允许 failed 行。可观测性有限流、熔断、Prometheus 文本指标和 Agent 运维页；运维页只聚合持久化行，不把进程内指标当历史，缺失 usage 或价格显示“部分未知”而不是 0。
>
> **验证与边界（30 秒）**：本轮实际执行了 Python 3.11/3.12 全量测试（253 passed，含 PostgreSQL 16）、Playwright 7 条 E2E、Docker 双镜像构建、HTTP 冒烟 15/15，以及 12 次真实 DeepSeek 调用。真实评测工具选择 6/6、参数 12/12、证据 2/2，但任务完成只有 1/3：履约被评分器误判，退款遇到网络错误。BGE-M3 因本机模型不可用标记 BLOCKED，没有用其他模型冒充。所有执行都是 simulation，不接真实广告/采购/支付。

---

## C. 当前架构图与核心请求链

### C1. 架构图

```text
React 工作台 (Vite, 5180)
  │  Bearer demo token / JWT
  ▼
FastAPI (8010)
  ├─ 中间件：request_id、限流、指标、统一错误
  ├─ 认证/授权：demo token 或 HS256 JWT → Actor(id, role, tenant_id)
  ├─ 路由：经营总览 / 分析 / 审批 / 执行 / 知识库 / 审计 / 运维
  │
  ├─ 分析任务队列（analyses 表）
  │    queued → running（条件 UPDATE 抢占 + lease）
  │      └─ 进程内 worker：心跳续租、超时、重试、死信、崩溃恢复
  │
  ├─ LangGraph 分析图（异常路径 10 步）
  │    读取数据 → 计算利润 → 归因 → 证据校验
  │    → 知识检索 → LLM/原生工具循环 → LLM 证据校验
  │    → 创建建议 → 等待审批（业务暂停点）
  │
  ├─ LangGraph 执行图（3 步，事务内）
  │    检查审批 → 模拟执行 → 保存结果与审计
  │
  ├─ 工具层：Tool Registry（9 个工具）
  │    只读工具 + 模拟执行工具；policy=safe/ask/deny
  │
  ├─ LLM 层：DeepSeek Provider + CircuitBreaker
  │    结构化 JSON / 原生 Function Calling / 降级状态
  │
  ├─ RAG：Markdown 切分 + embedding + 混合打分 + 引用校验
  │
  └─ SQLAlchemy → SQLite（默认）/ PostgreSQL 16（Compose）
       审计日志、执行记录、租户隔离、Alembic 迁移
```

### C2. 一次分析请求的核心链路

1. 前端 `POST /api/analyses`，带 `Idempotency-Key`。
2. 中间件分配 request_id，按 scope + IP + 身份限流，记录指标。
3. 认证依赖解析 Bearer：demo token 或 JWT；服务端读取用户、角色、租户。
4. 路由校验请求 Schema，按 `(tenant_id, idempotency_key)` 查重；重复返回原分析，内容不一致返回 409。
5. 插入 `status=queued` 的 Analysis 行，返回 202。
6. worker 用条件 UPDATE 抢占为 `running`，写入 `worker_id`、`attempts`、`lease_until`。
7. `run_analysis` 执行 LangGraph：每步更新 `steps` 和 `snapshot`；租约所有权检查保护每次写入。
8. `verify_evidence` 校验利润桥、数据完整性、证据 ID；数据不足则 `insufficient_data`，不创建建议。
9. `retrieve_knowledge` 检索 SOP；LLM 可选调用只读工具；`verify_llm_evidence` 校验 evidence/knowledge 引用。
10. 创建 `pending_approval` 建议；分析进入 `waiting_approval`，worker 释放租约。
11. 审批人 `POST /approve`：服务端角色校验 + 条件 UPDATE `pending_approval → approved` + 审计。
12. 执行人 `POST /execute`：条件 UPDATE `approved → executing`，检查/插入唯一 execution，运行执行图，写模拟结果、状态和审计；重复请求返回同一 execution，`replayed=true`。
13. 审计页按 `request_id` 或 `object_id` 追踪完整链路；运维页聚合任务、LLM、工具、tokens、成本和最近失败。

---

## D. 25 个高频问题（按优先级排序）

| # | 优先级 | 问题 | 主题 |
|---|---|---|---|
| 1 | 高 | 这个项目解决什么业务问题？为什么不是普通 BI？ | 业务定位 |
| 2 | 高 | 为什么利润必须由确定性代码计算，LLM 只做解释？ | 数字权威 |
| 3 | 高 | LangGraph 在你的项目里具体怎么用？为什么不是一串函数？ | LangGraph |
| 4 | 高 | 原生 Function Calling 的工具循环怎么防止模型乱调工具？ | Function Calling |
| 5 | 高 | 证据校验怎么做？模型编造 evidence ID 会怎样？ | 证据校验 |
| 6 | 高 | 审批与执行如何保证不被绕过、且幂等？ | 状态机/幂等 |
| 7 | 高 | 任务队列的 lease 和 fencing 是什么？为什么需要？ | 调度 |
| 8 | 高 | 多租户隔离怎么做？跨租户请求返回什么？ | 多租户 |
| 9 | 高 | JWT 认证与服务端角色校验怎么实现？为什么不信任 `X-Role`？ | JWT/RBAC |
| 10 | 高 | RAG 检索链路、BM25 与 RRF：你实际用了什么？ | RAG/BM25/RRF |
| 11 | 高 | 为什么默认 hashing embedding？BGE-M3 对比结果如何？ | BGE-M3 |
| 12 | 高 | 限流桶的 `max_buckets` 和 Bearer 轮换问题怎么修？ | 限流 |
| 13 | 高 | 熔断器怎么工作？哪些错误不计入？ | 熔断 |
| 14 | 高 | Token 成本怎么统计？为什么缺失 usage 不能显示 0？ | Token/成本 |
| 15 | 高 | 数据库迁移怎么保证旧库不丢数据？SQLite/PostgreSQL 差异？ | 迁移 |
| 16 | 中 | 为什么金额用整数美分？利润桥如何守恒？ | 金额/利润 |
| 17 | 中 | 数据不完整时 Agent 怎么处理？ | 数据质量 |
| 18 | 中 | 任务重试、死信、手动 retry 的边界？ | 重试 |
| 19 | 中 | 审计日志记录什么？能按 request_id 追吗？ | 审计 |
| 20 | 中 | Agent 运维页的数据来源是什么？为什么不用进程内 metrics？ | 运维页 |
| 21 | 中 | 前端如何展示降级状态、证据引用和空数据？ | 前端 |
| 22 | 中 | 测试体系怎么分层？真实 LLM 如何不进入 CI？ | 测试/CI |
| 23 | 中 | Docker 和 CI 怎么做的？验证到什么程度？ | Docker/CI |
| 24 | 中 | 为什么不用 MCP、多 Agent、向量数据库？要做怎么加？ | 架构取舍 |
| 25 | 中 | 这个项目最大的局限是什么？继续做两周先做什么？ | 局限/规划 |

---

## E. 逐题：30 秒回答、深挖追问、源码位置、现场演示

### Q1. 这个项目解决什么业务问题？为什么不是普通 BI？

**30 秒回答**：它解决“销售额上涨但利润下降，团队说不清原因、也不敢安全执行动作”的问题。BI 只展示指标；ProfitOps 把利润变化拆成八项会计贡献，强制对平，定位主要负向项，再用证据和知识库生成建议，最后必须人工审批才做模拟执行。它交付的是一条可追溯的决策链，不是一张图。

**深挖追问**：这和归因分析/因果推断有什么区别？
答：当前只做**会计贡献项归因**，不是因果推断。利润桥能说明“履约成本增加了 20,000 美分，是最大负向项”，但不能证明“履约涨价导致利润下降”。文案明确写“会计贡献项”，建议也是条件性情景，要求补充活动级 ROAS、履约报价单等证据。因果推断需要实验或更强的识别策略，当前没有做。

**源码位置**：`backend/app/services.py` 利润计算与利润桥；`backend/app/workflow.py:109` 分析入口；`docs/CONTRACT.md` 利润口径。

**现场演示**：打开经营总览，指出销售额上涨、净利润下降；展开利润桥，八项贡献合计与净利润变化一致；点开趋势图的数据表，说明图表和表格同源。

---

### Q2. 为什么利润必须由确定性代码计算，LLM 只做解释？

**30 秒回答**：因为 LLM 不擅长保证算术一致，也不能为金额负责。利润、环比、八项贡献、预算参数都由 Python/SQL 计算，同一份数据每次结果一致；LLM 只把已算好的事实组织成根因解释和策略文案。模型返回的金额不会覆盖确定性结果，建议标题、动作参数和预期影响仍由 `plan_action` 生成。

**深挖追问**：模型在文本里写错数字怎么办？
答：有三层：输入明确标注 `amount_unit=integer USD cents`；输出经过 Pydantic Schema 和数值/单位一致性门禁；金额权威仍以确定性代码为准。评测里还有 `number_check` 检查模型文本中的数字是否能在已知事实中找到。它不是对任意文本的逐字证明，但能拦住大多数金额/单位错误。

**源码位置**：`backend/app/services.py`；`backend/app/workflow.py` 的 `plan_action`/证据校验；`backend/evals/graders.py` 的 `check_numbers`。

**现场演示**：在分析结果页对比“净利润变化”和利润桥；再打开 LLM 面板，指出模型只输出 summary/root_causes/recommendations，金额和建议参数来自确定性层。

---

### Q3. LangGraph 在你的项目里具体怎么用？为什么不是一串函数？

**30 秒回答**：分析图把“读取数据、计算利润、归因、证据校验、知识检索、LLM、LLM 证据校验、创建建议、等待审批”组织成有状态节点；每个节点把步骤和快照写入数据库。`wait_for_approval` 是真实业务暂停点，不是内存等待；审批后从数据库快照进入独立执行图，不重跑建议。这样失败可恢复、步骤可追踪、审批边界清晰。

**深挖追问**：图状态存在哪里？进程重启会怎样？
答：图的业务状态持久化在 `analyses.steps`、`analyses.snapshot` 和 `result`，任务行本身在数据库中。进程重启后，启动恢复扫描把遗留 `queued/running` 按租约和重试规则处理；执行阶段从数据库快照重建，不依赖内存中的图对象。LangGraph 的 checkpoint 不是唯一事实来源，数据库行才是。

**源码位置**：`backend/app/workflow.py:109`（`run_analysis`）、`backend/app/workflow.py:483`（节点注册）；`backend/app/worker.py` 恢复逻辑。

**现场演示**：启动分析，观察工作台逐步出现 10 个步骤；刷新页面后步骤仍在；审批后执行阶段只出现 3 个执行步骤，说明没有重跑分析。

---

### Q4. 原生 Function Calling 的工具循环怎么防止模型乱调工具？

**30 秒回答**：模型只能看到三个只读工具：`get_profit_analysis`、`get_sku_breakdown`、`search_knowledge`。每次调用要同时通过名称允许列表、Registry 存在性、`policy=safe`、`effect=read|pure`、服务端角色、Pydantic 参数和 period/store 范围校验。循环还有最大轮次、最大调用数、重复调用上限和总预算；最后一轮强制 `tool_choice=none`。任何拒绝或异常都会终止循环并按降级策略处理，不会落到写工具。

**深挖追问**：如果模型重复调用同一个工具怎么办？
答：相同工具+参数重复超过 `max_repeats` 会返回 `llm_tool_repeated_call` 并终止；轮次超限返回 `llm_tool_round_limit_exceeded`。工具结果以 `role=tool` 回传，轨迹持久化在 `result.llm.tool_calls` 和 `rounds`，可以在工作台看到。

**源码位置**：`backend/app/llm_tools.py:36`（允许列表）、`:287`（执行与校验）、`:400`（`run_tool_loop`）；`backend/app/tools.py`（Registry）。

**现场演示**：打开一次真实/回放分析的 LLM 面板，展开工具轨迹，指出工具名、参数、结果摘要、轮次和终止原因；再展示 `/api/tools` 中每个工具的 policy。

---

### Q5. 证据校验怎么做？模型编造 evidence ID 会怎样？

**30 秒回答**：确定性层先生成 8 个 evidence（销售额、折扣、退款、采购、广告、履约、平台费、其他成本）。LLM 输出后，独立节点 `verify_llm_evidence` 检查 summary、root_causes、recommendations、evidence_references 里的所有 ID 是否属于本次证据集合；knowledge_references 必须属于本次预检索+工具检索合并后的 chunk_id 集合。任何未知 ID 都会让整次输出作废，按策略降级为确定性文案或让分析失败，不能进入审批流。

**深挖追问**：模型引用了正确 ID，但解释是错的呢？
答：证据 ID 校验保证“引用可追溯”，不保证语义正确。内容层还有主驱动命中、动作一致性、金额/单位一致性、禁止声明等门禁；真实评测里履约案例就是证据有效但动作类型内容门禁失败。语义忠实度需要人工抽样或 LLM Judge，当前没有声称已解决。

**源码位置**：`backend/app/workflow.py` 的 `verify_llm_evidence`；`backend/evals/graders.py` 的证据/内容门禁；`backend/tests/test_llm.py`、`test_native_evals.py`。

**现场演示**：在分析结果页展开“证据引用”，指出每个根因和建议都带 evidence ID；知识片段上有“✓ LLM 已引用”标记；无可靠答案时显示“无可靠答案”，不显示低分片段。

---

### Q6. 审批与执行如何保证不被绕过、且幂等？

**30 秒回答**：三层。第一，服务端从 Bearer 解析角色，`X-Role`/`X-Actor` 不参与身份判断；审批和执行都要求 approver。第二，状态机用条件 UPDATE 抢占：审批 `WHERE status='pending_approval'`，执行 `WHERE status='approved'`，rowcount 不是 1 就拒绝。第三，执行记录有 `executions.recommendation_id` 唯一约束和 `(tenant_id, idempotency_key)` 复合唯一；重复请求返回同一 execution，`replayed=true`。SQLite 下执行入口先 `BEGIN IMMEDIATE` 拿写锁，PostgreSQL 下条件 UPDATE 自带行锁。

**深挖追问**：两个请求同时执行会怎样？
答：只有一个能抢到 `approved → executing`；另一个等待后看到状态已变，读取已有 execution 并返回 `replayed=true`。测试用 6 个线程同时执行同一建议，断言 6 个响应同一 execution ID、只有 1 个非回放、数据库只有 1 条执行记录和 1 条成功审计。

**源码位置**：`backend/app/security.py:28`（`require_role`）；`backend/app/main.py:530`（approve）、`:538`（execute）；`backend/app/models.py:229`（执行幂等唯一约束）；`backend/tests/test_approval.py`。

**现场演示**：用分析师 token 调 `/approve`，返回 403；切换到审批人批准；执行后再次请求同一幂等键，返回同一 execution ID 和 `replayed=true`；审计页看到 `execution.replayed`。

---

### Q7. 任务队列的 lease 和 fencing 是什么？为什么需要？

**30 秒回答**：worker 用条件 UPDATE 把任务从 `queued` 抢成 `running`，写入 `worker_id`、`attempts` 和 `lease_until`；执行期间心跳续租。租约是写入许可：创建建议、持久化步骤/结果、写审计前的所有权确认和心跳续租，都在同一条条件 UPDATE 里要求 `status='running'`、`worker_id`、`attempts` 匹配且 `lease_until > 数据库当前时间`。租约过期后，即使旧 worker 还活着，也不能再写结果或续租；新 worker 可以接管。这就是 fencing。

**深挖追问**：fencing token 是什么？为什么不只用 worker_id？
答：当前用 `(worker_id, attempts)` 加 `lease_until` 作为所有权条件，不是独立的单调 fencing token。`attempts` 每次重新排队递增，配合 worker_id 和数据库时间比较，能挡住旧 worker 的过期写入；更严格的生产方案可以引入单调递增的 lease epoch，但当前测试覆盖了租约过期后的写入拒绝和崩溃恢复。

**源码位置**：`backend/app/worker.py:54`（`claim_job`）、`:78`（`claim_next_job`）、`:176`（恢复扫描）、`:403`（`finalize_job`）；`backend/tests/test_worker.py`（22 passed）。

**现场演示**：打开运维页的任务队列，指出 queued/running/retry/failed/dead_letter；在测试里展示租约过期后旧 worker 写入被拒绝、任务被重新接管。

---

### Q8. 多租户隔离怎么做？跨租户请求返回什么？

**30 秒回答**：stores、products、orders、daily_operations、analyses、recommendations、executions、audit_logs、knowledge_documents 都有 `tenant_id`；所有读取、列表、审批、执行、知识检索和审计查询先按 tenant 过滤。跨租户对象返回 404，不暴露是否存在。分析/执行的幂等键是 `(tenant_id, idempotency_key)` 复合唯一；知识文档的 active 唯一索引也是租户级，不同租户可以保存相同 SOP。

**深挖追问**：JWT 里的 tenant_id 能被篡改吗？
答：JWT 用 HS256 签名，篡改会验签失败；即使 token 内角色/租户字段被改，服务端每次请求还会重新读取用户、角色和租户状态，不信任 token 内的角色声明。测试覆盖跨租户读取 404、列表不泄漏、审批/执行隔离。

**源码位置**：`backend/app/security.py:98`（`decode_access_token`）；`backend/app/models.py` 各表 `tenant_id` 与唯一约束；`backend/tests/test_auth_tenant.py`（13 passed）；`frontend/e2e/acceptance.spec.ts` 跨租户 E2E。

**现场演示**：用 demo 分析师 JWT 读取 tenant-b 的分析，返回 404；用 tenant-b 账号读取同一 ID，返回 200；列表接口看不到对方数据。

---

### Q9. JWT 认证与服务端角色校验怎么实现？为什么不信任 `X-Role`？

**30 秒回答**：认证支持 demo mode 固定 Bearer token 和邮箱密码登录换取的 HS256 JWT。密码用带随机盐的 scrypt 哈希；JWT 携带 `sub/role/tenant_id/iss/iat/exp`，但服务端每次请求仍重新读取用户、角色和租户状态，不把 token 内的角色当作最终权限。角色只由服务端 `Actor` 决定，客户端传 `X-Role`/`X-Actor` 不参与判断。非 demo mode 必须显式配置 `AUTH_SECRET`，否则启动失败。

**深挖追问**：这是生产级 IAM 吗？
答：不是。没有 OIDC/SSO、密码重置、MFA、密钥轮换和租户管理后台；演示前端仍用 `/api/demo` 的固定 token 切换身份。它验证的是“服务端决定角色、JWT 验签、租户隔离”这条链路，不是完整的企业身份平台。

**源码位置**：`backend/app/security.py:52`（`hash_password`）、`:98`（JWT 解码）；`backend/app/main.py:240`（登录）、`:248`（me）；`backend/app/config.py` 的 `auth_secret` 校验。

**现场演示**：调用 `/api/auth/login` 获取 JWT，`/api/auth/me` 返回角色和租户；篡改 token 末三位后请求返回 401；用分析师 JWT 调审批接口返回 403。

---

### Q10. RAG 检索链路、BM25 与 RRF：你实际用了什么？

**30 秒回答**：实际链路是：6 篇合成 SOP → Markdown 标题切分（超长段按字符边界 + overlap）→ embedding → 按 `embedding_model` 和维度过滤 → 混合打分 `0.65*cosine + 0.35*词法 + 0.05*标题命中` → 候选重排加标题 boost → top_k；最高分低于 `min_score` 时 `no_answer=true` 且清空 hits。词法层是中文单字/双字/三字 token，单字低权重，**不是 BM25**；当前也**没有 RRF**，而是加权分数融合。

**深挖追问**：BM25 和 RRF 如果要做，怎么加？
答：BM25 需要词频、逆文档频率和文档长度归一化；当前词法层只有 token 重叠，没有 IDF。RRF 是对多个 ranked list 按 `1/(k+rank)` 融合，适合把向量检索和 BM25 的排名合并，不依赖分数尺度。要做的话，我会保留现有 42 条固定查询集和阈值，先分别测向量、BM25、加权融合、RRF 的 hit@1/hit@3/MRR，再做消融；不会直接替换现有链路。

**源码位置**：`backend/app/knowledge.py:655`（`search_knowledge`）、`:690`（打分公式）；`backend/evals/rag.py`（离线基线）；`backend/evals/rag_real.py`（42 查询对比）。

**现场演示**：在知识库页搜索“广告预算审批”，展示命中片段、分数、来源/版本；搜索“如何预订会议室午餐”，展示 `no_answer` 和空 hits。

---

### Q11. 为什么默认 hashing embedding？BGE-M3 对比结果如何？

**30 秒回答**：默认 hashing embedding 是 256 维确定性特征哈希，不联网、无额外依赖，测试/CI/演示完全可复现，适合作为工程基线。需要语义向量时切换 `KNOWLEDGE_EMBEDDING_PROVIDER=openai`，调用任意 OpenAI 兼容 `/embeddings`。BGE-M3 在本机 Ollama 未安装，`/v1/embeddings` 返回真实 404，`ollama pull` 因非公网重定向失败，所以标记 **BLOCKED**，没有用随机向量、mock 或 `qwen3-embedding` 冒充。Hashing 基线在 42 条固定查询上 hit@1 97.22%、hit@3 100%、MRR 0.9815、no-answer 100%、引用 100%。

**深挖追问**：那你怎么证明 BGE-M3 会更好？
答：不能证明。当前没有 BGE-M3 数据，所以不做“更好/更差”的结论。可复现的结论只有 hashing 基线在合成集上的表现。BGE-M3 可用后，用同一 42 条查询、同一阈值、独立临时库重跑，先不调参；如果要做 reranker 或多语言，再单独评测。

**源码位置**：`backend/app/knowledge.py:108`（Hashing）、`:139`（OpenAI-compatible）；`backend/evals/rag_real.py`；`docs/RAG_REAL_EMBEDDING_REPORT.md`；`docs/rag-real-embedding-2026-11-02.json`。

**现场演示**：运行 `rag_real.py --provider both`，展示 hashing 指标和 BGE-M3 的 `BLOCKED` 真实错误；强调没有伪造结果。

---

### Q12. 限流桶的 `max_buckets` 和 Bearer 轮换问题怎么修？

**30 秒回答**：原实现有两个问题：桶数量没有硬上限，攻击者可以用大量不同 key 撑爆内存；未认证的 Bearer token 轮换会不断生成新桶，绕过基础限流。修复是：`max_buckets` 作为硬上限，创建新桶前先清理超过 1 小时的陈旧桶，再按最旧访问时间淘汰，直到低于上限；限流身份只接受 demo token 或验签通过的 JWT，无效/缺失 token 统一落到匿名桶，登录 scope 完全忽略 Bearer。测试覆盖 10,000 个不同 key、并发访问、无效 Bearer、正常用户和登录请求。

**深挖追问**：这是分布式限流吗？
答：不是。桶是进程内状态，多副本部署必须换 Redis 或共享存储；当前单进程 API + 进程内 worker 的演示/单机部署不跨进程共享。文档和配置注释都明确了这个边界。

**源码位置**：`backend/app/observability.py`（`TokenBucketLimiter`）；`backend/app/security.py:145`（`rate_limit_identity`）；`backend/app/main.py:136`（中间件）；`backend/tests/test_observability.py`（20 passed，含新回归）。

**现场演示**：跑 `test_observability.py`，指出 10,000 key 后桶数量不超过上限、无效 Bearer 与匿名共享桶；用 curl 连续请求触发 429，展示 `Retry-After`。

---

### Q13. 熔断器怎么工作？哪些错误不计入？

**30 秒回答**：`CircuitBreakerProvider` 包在 LLM Provider 外层。连续可重试错误（429、5xx、超时、网络、空响应）达到阈值后打开熔断，冷却期内直接返回 `llm_circuit_open`，不再发网络请求；冷却后放行一个半开探测，成功关闭，失败重新打开。鉴权、余额不足、非法输出等确定性错误不计入熔断，避免掩盖配置问题。熔断错误仍走 `LLM_FALLBACK_POLICY`：`rules` 时确定性分析继续并标记降级，`fail` 时分析失败。

**深挖追问**：熔断状态怎么观测？
答：`result.llm` 持久化状态和错误码，审计记录 `analysis.llm_fallback`/`analysis.llm_failed`，`/metrics` 暴露熔断状态和限流命中；前端 LLM 面板显示“已降级为确定性规则”及原因。当前没有 OpenTelemetry 导出或跨进程聚合。

**源码位置**：`backend/app/llm.py`（`CircuitBreakerProvider`）；`backend/app/config.py` 的 `circuit_breaker_*`；`backend/tests/test_llm.py`。

**现场演示**：在测试里模拟连续 5xx，展示熔断打开后不再调用；前端展示降级面板和错误码。

---

### Q14. Token 成本怎么统计？为什么缺失 usage 不能显示 0？

**30 秒回答**：Provider 把每次真实返回的 usage（prompt/completion/total/cached）持久化到 `result.llm.usage`。成本用版本化单价 `PRICING_VERSION=2026-11-02` 计算：DeepSeek 缓存命中输入 $0.006/M、未命中 $0.30/M、输出 $1.20/M，按峰值估算。缺失 usage 或模型没有版本化价格时返回 `None`，运维页显示“部分未知”/“未知”，绝不显示 $0；只有全部运行都有价格时才显示 `complete`。真实评测 12 次调用总计 35,918 tokens，估算 US$0.009825。

**深挖追问**：这是账单吗？
答：不是。是公开列表价的估算，不是发票；传输失败的第 2 轮没有返回 usage，真实账单可能略高。缓存命中率、峰谷价、供应商折扣都没有建模。

**源码位置**：`backend/app/pricing.py:29`（`estimate_cost_usd`）；`backend/app/ops.py:148`（聚合）；`frontend/src/App.tsx` 的 ops 成本展示；`docs/AGENT_REAL_EVALUATION.md`。

**现场演示**：打开 Agent 运维页，展示“预估成本”和“部分未知”；在测试里插入缺 usage 的运行，确认金额为 null 而不是 0。

---

### Q15. 数据库迁移怎么保证旧库不丢数据？SQLite/PostgreSQL 差异？

**30 秒回答**：Alembic 有两个 revision：`0001_baseline` 用当前 metadata 建新库；`0002_tenant_scoped_idempotency` 先把 analyses/executions 的幂等键从全局唯一、`VARCHAR(100)` 改成 `(tenant_id, idempotency_key)` 复合唯一、`VARCHAR(200)`，并保留既有行。真正的前租户旧库先 `stamp 0001_baseline`，`0002` 会调用 `upgrade_legacy_schema` 补齐基线结构，再改约束。SQLite 需要关闭当前连接的外键、重建父表、复制行，再用 `PRAGMA foreign_key_check` 验证；PostgreSQL 用常规 `ALTER TABLE`。迁移原地完成，不删业务表、不清库。

**深挖追问**：启动时的兼容迁移和 Alembic 有什么区别？
答：`initialize_database` 的兼容迁移只补 `tenant_id`、任务字段和知识库部分唯一索引，方便现有演示库先启动；涉及幂等唯一约束的旧库必须运行 `python scripts/migrate.py upgrade head`，因为启动兼容迁移不会改这两个唯一约束。测试分别覆盖 SQLite 和 PostgreSQL。

**源码位置**：`backend/migrations/versions/0001_baseline.py:26`、`0002_tenant_scoped_idempotency.py:181`；`backend/app/db.py`；`backend/tests/test_migrations.py`、`test_postgres_migrations.py`（6 passed）。

**现场演示**：在临时 SQLite 和 PostgreSQL 16 上分别跑迁移测试；展示迁移后的列类型、复合唯一约束和行数保留。

---

### Q16. 为什么金额用整数美分？利润桥如何守恒？

**30 秒回答**：浮点数会出现精度误差，利润、折扣、退款、平台费需要精确对账，所以数据库和 API 全部用整数美分，前端只在展示时除以 100。利润率和环比用 Decimal 四舍五入到两位，分母为零返回 null。利润桥八项：销售额贡献 `current - previous`，其余成本项贡献 `-(current - previous)`；八项之和必须等于净利润变化，`verify_evidence` 会重算比对，`bridge_verified` 在 API 和 UI 可见。

**深挖追问**：退款和采购成本会不会重复扣？
答：不会。退款只作为负向收入扣减销售额；采购成本字段已经是退货后残值，不再对退款重复扣成本。这个口径写在 `docs/CONTRACT.md`，测试覆盖退款压力和负净销售额边界。

**源码位置**：`backend/app/services.py`；`docs/CONTRACT.md`；`backend/tests/test_metrics.py`、`test_workflow.py`。

**现场演示**：经营总览底部“贡献项合计与利润变化一致”；打开退款压力案例，指出退款只影响收入项。

---

### Q17. 数据不完整时 Agent 怎么处理？

**30 秒回答**：每日经营记录按“店铺 × SKU × 日期”检查覆盖；缺行或 `complete=false` 会产生具体缺失项。Agent 在 `verify_evidence` 阶段停止，状态 `insufficient_data`，不创建建议，也不启动 LLM 工具循环。空集不等于“零销售”，不会把缺失值当零。

**深挖追问**：真实评测里怎么验证“不启动模型”？
答：`insufficient_data` 案例的状态是 `not_requested`，`call_count=0`，模型调用 0 次，工具调用 0 次，安全门禁通过。这是 12 次真实调用里唯一没有消耗模型预算的案例，证明拒绝逻辑在真实 provider 路径上也生效。

**源码位置**：`backend/app/workflow.py` 的 `verify_evidence`/`insufficient_data` 分支；`backend/tests/test_workflow.py`；`backend/evals/llm_cases/insufficient_data.json`。

**现场演示**：在测试或评测报告里展示 `not_requested`、`call_count=0`；前端显示“数据不足”，没有建议按钮。

---

### Q18. 任务重试、死信、手动 retry 的边界？

**30 秒回答**：可重试错误（`workflow_failed`、`step_timeout`、`database_locked`、`worker_crashed`）按指数退避重试，达到 `max_attempts` 后写 `status=failed` 和 `error.dead_letter=true`；确定性失败不自动重试。手动 retry 只允许 `failed` 行，用单条带 `status='failed'` 条件的 UPDATE 转回 `queued`，避免读后写在并发重试或 worker 交错时把 running/completed 改回 queued。重试和死信都有审计事件，运维页展示重试次数、死信数和错误码。

**深挖追问**：重试会不会重复执行副作用？
答：分析阶段的建议创建有租约所有权和条件写入；执行阶段有唯一 execution 和幂等键。重试不会重复创建执行记录。手动 retry 只重置分析任务，不会重放已完成的执行。

**源码位置**：`backend/app/worker.py:117`（重试/死信规则）、`:403`（`finalize_job`）；`backend/app/main.py:447`（retry 路由）；`backend/tests/test_worker.py`（22 passed）。

**现场演示**：运维页展示失败任务、尝试次数、死信标记；点击“查看 Trace”跳到失败分析；在工作台手动重新排队，观察状态变化。

---

### Q19. 审计日志记录什么？能按 request_id 追吗？

**30 秒回答**：每条审计记录包含 actor、action、object_type、object_id、request_id、outcome、details 和时间。审批、执行、回放、失败、被拒绝的请求、LLM 成功/降级/失败、重试调度、死信都会记录；业务写与审计尽量在同一事务，拒绝请求用独立事务补记。`GET /api/audit-logs?request_id=...` 精确过滤，也支持 `object_id` 查某个建议的全链路。

**深挖追问**：审计能防篡改吗？
答：当前是应用级审计，没有防篡改存储、哈希链、日志外送或独立权限控制。生产需要追加写入、WORM/对象锁、集中日志和保留策略。当前能证明的是“同一条链路可追踪”，不是“审计不可抵赖”。

**源码位置**：`backend/app/audit.py`；`backend/app/main.py:567`（审计路由）；`backend/tests/test_approval.py`；前端审计页。

**现场演示**：执行一次审批+模拟执行，复制请求 ID，在审计页过滤，展示 approve/start/executed/replayed 事件。

---

### Q20. Agent 运维页的数据来源是什么？为什么不用进程内 metrics？

**30 秒回答**：`GET /api/ops/summary` 只聚合持久化数据库行：analyses、recommendations、executions、audit_logs。它按 tenant 过滤、只允许 approver，窗口 1–168 小时；返回任务状态、重试/死信、错误码、LLM 状态、工具调用/失败/轮次、tokens、版本化成本、审批/执行当前状态和最近失败。进程内 Prometheus 指标不进入这个页面，因为重启会归零，不能伪装成历史；页面会明确写“数据来自持久化行”。缺失 usage 或价格显示“部分未知”，不显示 0。

**深挖追问**：为什么限制 approver？
答：运维数据包含跨团队失败详情和成本，分析师不需要看到全租户的失败细节；接口按 JWT tenant 过滤，analyst 返回 403。前端也按角色隐藏页面，但服务端才是权限边界。

**源码位置**：`backend/app/ops.py:43`（`build_ops_summary`）；`backend/app/main.py:554`（路由）；`frontend/src/App.tsx` 的 ops 页面；`backend/tests/test_ops.py`（4 passed）；`frontend/e2e/acceptance.spec.ts` 运维 E2E。

**现场演示**：用 analyst JWT 调 `/api/ops/summary` 返回 403；切换 approver 打开“Agent 运维”，展示任务队列、模型/工具、tokens/成本、最近失败；点击“查看 Trace”跳到失败分析。

---

### Q21. 前端如何展示降级状态、证据引用和空数据？

**30 秒回答**：LLM 面板显示 `llm_success`、`deterministic_fallback`、`llm_failed`、`not_requested` 四种状态；降级时明确写“不是 LLM 输出”并显示原因/错误码；Token 只在 API 真实返回时显示。证据引用显示 evidence ID 和知识片段的“✓ LLM 已引用”；无可靠答案显示“无可靠答案”。空数据、加载中、API 错误（带 request ID）都有独立状态；运维页无数据时显示“暂无数据”，不画假图。

**深挖追问**：前端如何保证和后端契约一致？
答：`frontend/src/types.ts` 与 API Schema 同步；Vitest/RTL 用真实响应形状测试；Playwright 用真实后端跑关键链路。契约变更必须同步后端测试、`types.ts` 和 `docs/CONTRACT.md`。

**源码位置**：`frontend/src/App.tsx`、`frontend/src/types.ts`、`frontend/src/api.ts`、`frontend/src/App.test.tsx`（16 passed）。

**现场演示**：切换深色模式；打开降级分析，指出“已降级为确定性规则”；打开知识库无答案查询；打开运维页空租户显示“暂无数据”。

---

### Q22. 测试体系怎么分层？真实 LLM 如何不进入 CI？

**30 秒回答**：后端 pytest 覆盖利润/证据/状态机/审批幂等/租户/JWT/worker lease/fencing/死信/迁移/限流/熔断/ops；前端 Vitest+RTL 覆盖交互和降级/空数据；Playwright 跑真实浏览器和真实 API；评测分离线 FakeProvider、原生离线、回放、真实 DeepSeek 四类。CI 固定空 `LLM_API_KEY` 和临时测试数据库，只跑离线和单元测试；真实 LLM 必须显式授权字符串 + 正整数预算，单独执行并记录调用次数和报告。

**深挖追问**：测试是不是自己写自己过？
答：测试是仓库的一部分，但本轮实际执行了 253 个后端测试（Python 3.11/3.12，含 PostgreSQL 16）、7 条 Playwright、15/15 HTTP 冒烟、Docker 双镜像构建和 12 次真实 DeepSeek 调用。真实调用有原始报告和脱敏 JSON；失败（履约内容门禁、退款网络错误、BGE-M3 BLOCKED）都保留，没有删除或改成通过。

**源码位置**：`backend/tests/`；`frontend/src/App.test.tsx`；`frontend/e2e/acceptance.spec.ts`；`.github/workflows/ci.yml:19/65/109/136`。

**现场演示**：跑 `pytest backend/tests -q`、`npm --prefix frontend test`、`npm --prefix frontend run test:e2e`；展示 CI 配置中的空 `LLM_API_KEY`。

---

### Q23. Docker 和 CI 怎么做的？验证到什么程度？

**30 秒回答**：后端镜像是 `python:3.12-slim` + 锁定依赖 + app/migrations；前端是多阶段 `node:22-alpine` 构建 + `nginx:1.27-alpine`。CI 有 Python 3.12/3.11 两个后端 job、前端测试+构建 job、后端/前端镜像构建 job；自动 CI 使用空 `LLM_API_KEY`，不跑真实模型。本轮实际构建了两个镜像成功；没有把镜像构建等同于 Compose 运行验收。

**深挖追问**：Compose 跑过吗？
答：历史记录里 2026-10-31 在本机 Docker Desktop 跑过 PostgreSQL 16 + 后端 + nginx，后端直连和代理各 11/11 冒烟；本轮只重新构建镜像，没有重新 `docker compose up`，所以不声称本轮容器运行通过。其他机器仍需复验。

**源码位置**：`backend/Dockerfile`、`frontend/Dockerfile`、`compose.yaml`、`.github/workflows/ci.yml`；`docs/TEST_REPORT.md`。

**现场演示**：展示两个镜像构建成功；打开 CI 配置，指出 Python 3.11/3.12 和空 Key；说明本轮未推送，CI 结果待推送后确认。

---

### Q24. 为什么不用 MCP、多 Agent、向量数据库？要做怎么加？

**30 秒回答**：当前是单应用、单进程 worker 的 MVP，工具在内部 Tool Registry 里，服务端统一做 allowlist、policy/effect、角色和参数校验；引入 MCP 会增加进程边界和认证面，但不改变业务收益，所以没做。多 Agent 会把一个已经能闭环的分析/审批/执行流程拆成分布式协调问题，当前不需要。6 篇合成 SOP 用 SQLite + 内存打分足够，pgvector/Chroma/Milvus 在这个规模没有收益。MCP、BM25、RRF、BGE-M3、reranker 都明确标注为未实现或 BLOCKED，没有包装成已有能力。

**深挖追问**：如果要做 MCP，怎么保证安全？
答：只暴露只读工具，MCP server 侧做认证和租户绑定，客户端仍不能绕过服务端 policy；工具结果视为不可信数据，不能触发审批/执行；写工具不暴露。需要单独的集成测试和权限测试，不能直接把现有 Registry 全量开放。

**源码位置**：`backend/app/tools.py`、`backend/app/llm_tools.py:36`；`docs/ARCHITECTURE.md` 的边界说明；`docs/INTERVIEW.md` 的“不要声称”清单。

**现场演示**：打开 `/api/tools`，指出 policy=safe/ask/deny 和没有 deny 工具；说明 MCP 不在代码里。

---

### Q25. 这个项目最大的局限是什么？继续做两周先做什么？

**30 秒回答**：最大局限是“真实性与规模”：真实 LLM 评测只有 3 个正常合成案例、6 次模型运行；BGE-M3 因环境 BLOCKED；RAG 只有 6 篇合成 SOP；认证是自建 JWT，不是生产 IAM；限流是单进程内存桶；任务队列是进程内 worker；Docker 只验证了镜像构建，没有本轮 Compose 运行；没有真实广告/采购/支付，全部 simulation。继续做两周，我会先补 BGE-M3 和真实语料评测、扩大 LLM 案例和人工语义抽样，再做分布式限流/独立 worker/OIDC，最后才考虑 MCP 或多 Agent。

**深挖追问**：为什么不先做多 Agent？
答：当前瓶颈不是“Agent 数量”，而是评测真实性和生产运行能力。多 Agent 会增加协调、幂等、可观测性和成本，但不会自动提升利润归因的准确性。先把单 Agent 的工具选择、证据、成本、失败恢复做扎实，再拆才有意义。

**源码位置**：`docs/AGENT_REAL_EVALUATION.md`、`docs/RAG_REAL_EMBEDDING_REPORT.md`、`docs/TEST_REPORT.md` 的未验证清单；`docs/ARCHITECTURE.md` 的边界。

**现场演示**：展示真实评测报告的失败项和 BGE-M3 BLOCKED；展示运维页和审计页，说明已有能力与边界的区别。

---

## F. 必须覆盖主题索引

| 主题 | 对应问题 | 实际状态 |
|---|---|---|
| LangGraph | Q3 | 已实现并测试；分析图/执行图 |
| Function Calling | Q4 | 已实现；离线 37 场景 + 真实 12 次调用 |
| MCP | Q24 | **未实现**；只讲取舍 |
| RAG | Q10、Q11 | 已实现；6 篇合成 SOP、42 条查询 |
| BGE-M3 | Q11 | **BLOCKED**；没有真实指标 |
| BM25 | Q10 | **未实现**；当前是 token 重叠词法层 |
| RRF | Q10 | **未实现**；当前是加权分数融合 |
| 证据校验 | Q5 | 已实现；未知 ID 作废/降级 |
| 模型评测 | Q11、Q22 | 离线 + 真实；样本小，失败保留 |
| 任务调度 | Q7、Q18 | 持久化队列 + lease/fencing/重试/死信 |
| Lease | Q7 | 已实现；条件 UPDATE + 心跳 |
| Fencing | Q7 | 用 `(worker_id, attempts, lease_until)` 所有权条件；非独立 epoch |
| 幂等 | Q6 | 分析/执行复合唯一键；6 线程测试 |
| JWT | Q9 | HS256 + scrypt；服务端重读角色 |
| 多租户 | Q8 | 全表 tenant_id；跨租户 404 |
| 限流 | Q12 | 固定容量令牌桶；单进程 |
| 熔断 | Q13 | 可重试错误计数；半开探测 |
| Token 成本 | Q14 | 版本化单价；缺失显示未知 |
| 数据库迁移 | Q15 | Alembic 0001/0002；SQLite/PostgreSQL 测试 |
| Docker | Q23 | 双镜像构建通过；本轮未 Compose 运行 |
| CI | Q22、Q23 | 3.11/3.12、前端、镜像；本轮未推送 |

---

## G. 设计/模拟 vs 真实验证

### G1. 已由真实执行验证

- 确定性利润计算、利润桥守恒、整数美分、退款/采购不重复扣：后端测试与评测。
- LangGraph 分析/执行流程、步骤持久化、审批暂停：后端测试、Playwright、HTTP 冒烟。
- 审批/执行条件 UPDATE、唯一执行记录、租户级幂等：后端测试（含 6 线程并发）、E2E。
- Worker 条件抢占、心跳、租约过期写入拒绝、重试、死信、崩溃恢复：`test_worker.py` 22 passed。
- JWT 登录、验签、服务端角色、跨租户 404：`test_auth_tenant.py` 13 passed、Playwright。
- SQLite/PostgreSQL 迁移：6 passed（本机 PostgreSQL 16）。
- 限流硬上限、Bearer 轮换、10,000 key、并发：`test_observability.py` 20 passed。
- Agent 运维页真实聚合、角色限制、Trace 跳转：`test_ops.py` 4 passed、Playwright。
- 原生工具循环离线 37 场景：3/3 正常、34/34 负向对照。
- 真实 DeepSeek 12 次调用：工具选择 6/6、参数 12/12、证据 2/2、0 副作用；任务完成 1/3。
- Hashing RAG 42 条查询：hit@1 97.22%、hit@3 100%、no-answer 100%、引用 100%。
- HTTP 冒烟 15/15；前端单测 16 passed；生产构建；Docker 双镜像构建。

### G2. 仅模拟/设计

- 所有广告、采购、支付、ERP 动作：**simulation**，没有真实外部副作用。
- 业务动作参数和预期影响：条件性情景测算，不是收益承诺。
- 认证：自建 JWT/demo token，不是生产 IAM；没有 OIDC/SSO/MFA/密钥轮换。
- 限流：单进程内存桶，不是分布式限流。
- 任务队列：进程内 worker + 数据库队列；不是独立消息队列/分布式调度。
- 可观测性：进程内 Prometheus 文本指标；没有 OpenTelemetry、跨进程聚合、长期存储。
- 知识库：6 篇合成 SOP；没有真实业务文档、PDF/OCR、文档级 ACL、reranker、大规模向量库。
- 多 Agent、MCP、BM25、RRF：**未实现**。
- BGE-M3：**BLOCKED**，没有真实指标。

### G3. 本轮未验证

- 推送后的 GitHub Actions 结果：**NOT RUN**（本轮尚未推送）；不把本地结果写成 CI 结果。
- 本轮 `docker compose up` 运行：**NOT RUN**；只验证了双镜像构建。
- BGE-M3 真实检索：**BLOCKED**。
- 生产规模并发、分布式限流、跨进程队列、真实业务语料：**未验证**。

---

## H. 10 个最可能被质疑的问题（诚实回答）

### H1. “这是不是包装过的规则引擎，LLM 只是噱头？”

答：规则引擎是核心，这是有意的。利润、证据、金额、审批、执行不能交给模型；LLM 的增量是根因归纳、策略文案和按需工具查询。真实评测里模型确实主动调用了两个只读工具并按顺序完成，说明它不是纯装饰；但我也明确说，当前 LLM 不是利润计算的权威，失败会降级。这个项目的卖点是“可控的决策链”，不是“模型替代规则”。

### H2. “12 次调用、3 个案例，任务完成率只有 1/3，凭什么说可用？”

答：我不说“生产可用”。我说的是“真实链路跑通，工具选择和参数在 6 次运行里 6/6、12/12 正确，证据 2/2 有效，0 副作用；任务完成 1/3，失败原因是评分器误判和网络错误，不是安全问题”。样本小，不能外推准确率；报告保留了全部失败。下一步是扩大案例和重跑，不是把 1/3 说成成功。

### H3. “履约失败是不是模型不行？你是不是改了评分器让它过？”

答：没有改。履约最终输出的动作是 `create_review_task`，但它同时写了“保持日预算 184 美分不变”，固定评分器的预算调整正则把这句话识别成预算调整，所以 `action_type_consistency=false`。这是评分器误判，模型没有提出预算调整。我保留 FAIL，并在报告里写清楚原因；没有事后重打分、没有删除案例。这个样本正好说明评测器也需要被评测。

### H4. “RAG 只有 6 篇合成文档，BGE-M3 还没跑，凭什么叫 RAG？”

答：它具备完整的 RAG 链路：文档入库、Markdown 切分、embedding、混合检索、`no_answer`、chunk 引用校验、LLM 只能引用允许的 chunk_id。但我不声称通用语义检索能力；BGE-M3 是 BLOCKED，hashing 是工程基线。42 条固定查询的 97.22% hit@1 只在这个合成集上成立，不是业务语料准确率。真实业务文档、PDF/OCR、reranker 都还没做。

### H5. “多租户/JWT 是自建的，能算生产级吗？”

答：不能。它是可运行的自建账号体系，验证了服务端角色、JWT 验签、租户过滤、跨租户 404 和幂等隔离；但没有 OIDC/SSO、密码重置、MFA、密钥轮换、租户管理后台。生产要接企业身份提供商，并把角色/租户从可信 token 映射到服务端权限，不能把这套 demo 认证直接上线。

### H6. “进程内 worker 也算持久化队列？宕机怎么办？”

答：任务是持久化的数据库行，状态、步骤、快照、lease、attempts 都在数据库；worker 是进程内循环，不是独立消息队列。进程崩溃后，过期租约会被恢复扫描重新排队或进入死信，旧 worker 的写入被 fencing 拒绝；但多副本/独立 worker 的部署形态没有验证。生产可以保留同一套数据库状态机，把 worker 拆成独立进程，并考虑 SKIP LOCKED 或消息队列。

### H7. “限流是单进程内存桶，多副本怎么办？”

答：当前明确是单进程方案，适合单机演示；多副本必须换 Redis/共享存储，否则每个副本各算各的。`max_buckets` 硬上限和 Bearer 轮换修复解决的是单进程内存安全和绕过问题，不是分布式一致性。文档和配置注释都写了这个边界。

### H8. “执行都是 simulation，业务价值在哪里？”

答：价值在“决策链”而不是“真实副作用”：把利润归因、证据、知识、LLM 解释、审批、幂等、审计串起来，让团队能安全地评审一个动作，再决定是否接入真实系统。接真实广告/采购需要额外的权限、风控、回滚、对账和供应商 API，当前明确不做。模拟执行证明的是状态机、事务、幂等和审计，不是业务收益。

### H9. “这些测试是不是自己写自己过？有没有真实浏览器/数据库/模型？”

答：测试是仓库的一部分，但本轮实际执行了：Python 3.11 和 3.12 全量 253 passed（含 PostgreSQL 16）、Playwright 7 passed、HTTP 冒烟 15/15、Docker 双镜像构建、12 次真实 DeepSeek 调用。真实调用有原始报告和脱敏 JSON；BGE-M3 失败、履约内容门禁失败、退款网络错误都保留。没有把本地测试写成 GitHub CI 结果；CI 要等推送后执行。

### H10. “线上 LLM 超时/熔断，用户看到什么？会不会静默失败？”

答：不会静默。`result.llm` 持久化 `deterministic_fallback`/`llm_failed`/`not_requested` 和错误码；前端明确显示“已降级为确定性规则”或“LLM 调用失败”，并说明降级结果不是 LLM 输出；审计记录 `analysis.llm_fallback`/`analysis.llm_failed`；运维页统计 LLM 状态、错误码和失败任务。熔断打开时返回 `llm_circuit_open`，按 `LLM_FALLBACK_POLICY` 决定继续规则分析还是让分析失败。用户不会看到伪造的 LLM 成功。

---

## 附：现场演示清单（按 3 分钟版本）

1. `python scripts/dev.py` 启动；打开 `http://127.0.0.1:5180`。
2. 经营总览：销售额上涨、净利润下降、利润桥对平。
3. 启动 Agent：10 个步骤、请求 ID、LLM 降级/成功状态。
4. 查看建议：证据 ID、知识片段引用、待执行模拟动作参数。
5. 分析师调 `/approve` → 403；切换审批人 → 批准。
6. 模拟执行 → `SIMULATION`、外部调用 0；再次请求 → 同一 execution ID、`replayed=true`。
7. 审计页按 request ID 过滤。
8. Agent 运维页：任务队列、LLM/工具、tokens/成本、最近失败、Trace 跳转。
9. 知识库：命中、分数、`no_answer`。
10. 收尾：打开 `docs/AGENT_REAL_EVALUATION.md`、`docs/RAG_REAL_EMBEDDING_REPORT.md`、`docs/TEST_REPORT.md`，主动说明失败与 BLOCKED。

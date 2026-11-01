# 架构与关键决策

## 运行边界

```
React 工作台 (5180) --真实 HTTP--> FastAPI (8010) --SQLAlchemy--> SQLite / PostgreSQL
                                          |
                                          +-- LangGraph 分析图（异常路径 10 个持久化步骤）
                                          +-- LangGraph 执行图（3 个步骤，事务内）
                                          +-- Tool Registry（9 个 Pydantic 工具）
                                          +-- 知识库 RAG（Markdown 切分 + 向量/词法混合检索 + 引用校验）
                                          +-- LLM Provider（DeepSeek，可选，超时/重试/结构化输出/原生工具调用）
                                          +-- 审计事件 + 请求 ID
```

默认不依赖 LLM API Key，也不调用真实广告、采购或支付系统。分析结论由确定性规则和数据库账本产生；`engine=deterministic-rules-v1` 在分析、建议和健康接口中可见。配置 `LLM_API_KEY` 后，DeepSeek 只参与根因解释和策略文案，`engine` 在真实成功且证据校验通过时标记为 `deepseek-llm-v1`。这里不是“模型假装成规则”，也没有把 mock 或规则结果冒充真实 LLM。

## 为什么选择 SQLite 默认、PostgreSQL 可选

面试演示需要一条无需外部服务即可复现的路径，因此本地默认 SQLite（WAL、外键、30s busy timeout）。生产式容器路径使用 PostgreSQL 16 + psycopg，Compose 提供健康检查和持久卷。数据库事实模型、唯一约束和条件 UPDATE 在两者通用。2026-10-31 已在本机 Docker Desktop 完成 Compose 实际运行验收（PostgreSQL 16 + 后端 + nginx 前端，后端直连与 nginx 代理各 11/11 HTTP 冒烟通过）；其他机器仍需复验，详见 `docs/TEST_REPORT.md`。

## 利润口径与归因

金额在数据库和 API 中均为整数美分，避免浮点误差。订单成本字段彼此独立；广告支出来自 `daily_operations`，不在订单中重复。退款仅扣减收入，采购成本已经是退货后残值，避免重复扣减。利润率与环比使用 Decimal 四舍五入到两位；分母为零返回 null，不伪造 0%。利润桥的每项贡献为：

- 销售额：`current - previous`
- 折扣、退款、采购、广告、履约、平台费、其他：`-(current - previous)`

八项之和必须等于净利润变化；否则证据校验失败。主要驱动按负向会计贡献排序，文案明确为“利润桥归因”，不声称因果。

## 工作流与持久化

分析是持久化任务而不是只存在于进程内的 BackgroundTasks。`POST /api/analyses` 先提交 queued 行，worker 用条件 UPDATE 抢占为 running，记录 `attempts`、`worker_id` 和 `lease_until`；执行期间心跳续租，进程崩溃后过期租约在启动或轮询时重新排队。`run_analysis` 执行 LangGraph 分析图，每个节点开始和完成时更新 `analyses.steps` 与 `snapshot`；`wait_for_approval` 是真实的业务暂停点，不是内存等待。执行阶段从数据库快照进入单独执行图，读取审批状态、调用唯一模拟执行工具并保存结果，避免重跑建议生成。失败会写入 `failed` 状态、失败步骤和错误码；`workflow_failed`、`step_timeout`、`database_locked`、`worker_crashed` 按指数退避重试，达到上限后标记 `dead_letter`，确定性失败不自动重试。手动 retry 只允许 failed 行，并用单条带 `status='failed'` 条件的 UPDATE 完成状态转换，避免读后写在并发重试或 worker 交错时把 running/completed 改回 queued。`run_analysis` 写入 failed 后、`finalize_job` 之前进程退出时，行上仍保留 `worker_id` 和过期租约；恢复扫描按同一重试/死信规则处理这些行，并标记 `recovered_before_finalize=true`，因此失败写入本身不会造成永久卡死。超时在步骤完成后检查实际耗时（`step_timeout`），不声称能够强杀同步线程。

## LLM 推理层

`backend/app/llm.py` 定义统一 Provider 语义：`generate()` 返回文本、模型、耗时、Token 用量、尝试次数；`generate_json()` 额外要求 OpenAI 兼容的 `response_format={"type":"json_object"}`；`chat()` 接受 `tools`/`tool_choice` 并解析 assistant 的 `tool_calls`（`id`、`type=function`、`function.name`、`function.arguments`）。`DeepSeekProvider` 使用 `httpx`，基址默认 `https://api.deepseek.com`，请求 `POST /chat/completions`，模型默认 `deepseek-flash`（DeepSeek-V4.1-Flash-1017）。401/403/402 不重试，429/5xx/超时/网络错误/空响应按 `LLM_MAX_RETRIES` 有界重试；每次错误映射稳定错误码，并记录尝试次数和耗时。空 `content` 只有在存在 `tool_calls` 时才算有效响应。

LLM 节点位于确定性利润、异常归因和证据校验之后、创建建议之前。输入由 `build_llm_payload` 从已计算结果构造，包括周期、总额、变化、主要驱动、8 项证据、紧凑 SKU 摘要和 `plan_action` 已确定的模拟动作；金额字段为整数美分并显式标注 `amount_unit`。提示词要求只引用给定证据、不编造数字、不承诺收益、保持 simulation 边界、限制各字段长度，并输出严格 JSON。启用 `LLM_NATIVE_TOOLS` 时，`backend/app/llm_tools.py` 执行有界循环：普通轮 `tool_choice=auto`，最后一轮强制 `none`；模型请求的工具必须同时通过显式名称允许列表（`get_profit_analysis`、`get_sku_breakdown`、`search_knowledge`）、Registry 存在性、`policy=safe`、`effect=read|pure`、服务端角色、Pydantic 输入 Schema 和本次 period/store 范围校验；`search_knowledge` 没有 period，范围校验只对带周期的工具生效。工具由服务端读取可信账本并用既有指标函数计算，模型只提交 `MetricsInput`，不能提交 `RawMetrics` 或完整报告。工具结果以 `role=tool` 消息回传，循环受轮次、调用次数、重复次数、总预算和结果字节数限制；任何拒绝或异常都会终止循环并按降级策略处理，不会落到写工具。`result.llm` 持久化 `native_tools`、`termination_reason`、逐轮 `rounds` 和逐次 `tool_calls`。不支持 `chat()` 的 FakeProvider 继续使用单次 `generate_json` 路径。

调用使用可配置的 `LLM_MAX_TOKENS`（默认 4000）；Provider 记录 `finish_reason`，截断导致的非法 JSON 会在错误信息中明确标注。最终输出经 `LLMAnalysisOutput`（Pydantic）校验；独立节点 `verify_llm_evidence` 检查 `summary/root_causes/recommendations/evidence_references` 中的全部证据 ID 是否属于本次 `findings.evidence`，并检查 `knowledge_references` 是否属于本次预检索与工具检索合并后的 chunk_id 集合。任何未知 ID 都会使本次 LLM 输出作废，不能进入审批流。

数字权威性没有转移：建议标题、动作参数、预算和预期影响金额仍由确定性 `plan_action` 生成；LLM 成功时只把 `summary` 用于建议的“问题”描述，并附加根因、风险、待补充信息和置信度。`recommendations.body.engine` 与 `analysis.result.engine` 只有在 `llm_success` 时为 `deepseek-llm-v1`。降级状态完整持久化：无 Key 为 `deterministic_fallback/not_configured`，调用或证据失败为 `deterministic_fallback` 并带错误码，`LLM_FALLBACK_POLICY=fail` 时为 `llm_failed` 且分析失败，无异常/数据不足为 `not_requested`。审计分别记录 `analysis.llm_succeeded`、`analysis.llm_fallback`（degraded）和 `analysis.llm_failed`。LLM 步骤不叠加通用 15s 步骤超时，由 Provider 的 `LLM_TIMEOUT` + 有界重试负责；原生工具循环另受 `LLM_TOOL_BUDGET_SECONDS` 总预算约束。

## 知识库 RAG

`backend/app/knowledge.py` 把运营 SOP 作为服务端只读上下文接入分析图。文档与片段分表存储：文档保留标题、来源、版本、生效日期、正文 SHA-256 和 `active`，片段保留 Markdown 标题、正文、字符数、embedding 与模型名。创建时相同正文 SHA-256 幂等返回已有文档；数据库以仅覆盖 active 行的部分唯一索引保证并发上传不产生重复 active 文档，旧库启动时先软删除重复行再建索引，不删除历史；删除是软删除，保留审计和历史；`knowledge-v1` Seed 只在首次插入 6 篇合成 SOP，重复执行不重复插入，也不删除用户文档。

离线默认使用确定性特征哈希 embedding（256 维），不联网、无额外依赖，因此测试、CI 和演示完全可复现。需要语义向量时切换 `KNOWLEDGE_EMBEDDING_PROVIDER=openai`，调用任意 OpenAI 兼容 `/embeddings` 端点（Ollama、vLLM 或私有网关），响应按 index 排序并 L2 归一化；失败映射为 502 `knowledge_embedding_failed`，不会静默回退到另一个模型。检索按 `embedding_model` 与向量维度过滤，换模型或同模型换维度后旧向量不参与，避免把不同向量空间的距离混算；已有文档通过重新上传相同正文或显式 reindex 重嵌入现有 chunk（保留文档与 chunk ID/正文），重复执行幂等。

切分按 Markdown 标题分段，超长段按字符边界切分并保留 overlap（默认 600/100）。检索打分是混合式：`0.65 * cosine + 0.35 * 词法 + 0.05 * 标题命中`，先取 `candidate_k`，再用文档标题命中 `+0.03` 重排，返回 `top_k`。词法层对中文使用单字、双字、三字 token，单字权重低，避免“预/订”这类单字造成无关命中。最高分低于 `KNOWLEDGE_MIN_SCORE` 时 `no_answer=true` 且 hits 清空，低置信度片段不能成为引用来源。

工作流在 `verify_evidence` 之后、LLM 之前执行 `retrieve_knowledge`；查询由确定性指标和主要驱动构造，检索失败只写 `knowledge_error`，不阻断确定性分析。LLM 可以额外调用 `search_knowledge` 工具补充检索，工具结果与预检索结果按 chunk_id 去重合并。知识片段在提示词中被明确标记为不可信数据、单条截断到 700 字符；模型只能引用 `allowed_knowledge_ids` 中的 chunk_id，未知引用会让整次 LLM 输出作废并降级。知识内容不能触发工具调用、审批或执行，也不能改变金额。

离线评测 `backend/evals/rag.py` 使用临时 SQLite、固定查询集和 hashing embedding，报告 hit@1/hit@3、no-answer 准确率、引用有效性与延迟，任一硬门禁失败时退出码非 0。本次 6 篇文档、8 个查询的结果是 hit@1 83.33%、hit@3 100%、no-answer 100%、citation validity 100%，网络调用 0 次。它验证的是检索与引用链路在合成知识库上的行为，不是通用语义检索准确率。

## 状态机与并发

`proposed → pending_approval → approved → executing → executed`，另有 `rejected` 和 `execution_failed` 终态。审批和执行都通过带旧状态条件的 UPDATE 抢占状态；执行先抢占行锁/写锁，再检查唯一执行记录，最后在同一个事务中插入执行记录、模拟变更、更新状态和写审计。`executions.recommendation_id` 唯一，`(tenant_id, idempotency_key)` 复合唯一。重复请求返回已有执行记录并标记 `replayed=true`；不同建议复用执行键返回 409。失败保留错误诊断并返回 HTTP 500，不会伪装成成功。

## 权限与安全边界

认证支持两种路径：demo mode 的固定 Bearer token，以及邮箱密码登录换取的 HS256 JWT。密码使用带随机盐的 scrypt 哈希；JWT 只携带 `sub/tenant_id/iss/iat/exp`，服务端每次请求都重新读取用户、角色和租户状态，不信任 token 内的角色声明。`AUTH_SECRET` 在非 demo mode 下必须显式配置，否则启动失败。客户端传入的 `X-Role`、`X-Actor` 不参与身份判断。

多租户隔离覆盖 stores、products、orders、daily_operations、analyses、recommendations、executions、audit_logs 和 knowledge_documents：所有读取、列表、审批、执行、知识检索和审计查询都先按 `tenant_id` 过滤，跨租户对象返回 404，不暴露是否存在。知识文档的部分唯一索引改为 `(tenant_id, content_sha256)`，不同租户可以各自保存相同 SOP；非 demo 租户的 idempotency key 在存储时加租户前缀，数据库再以 `(tenant_id, idempotency_key)` 复合唯一约束兜底，存储列宽 200，因此 100 字符客户端键加租户前缀不会超限，同一存储字符串也可被不同租户各自使用。Tool Registry 和业务服务都做角色校验，普通分析师即使调用 API 或隐藏前端按钮也无法批准或执行。

工具在 `/api/tools` 中显式声明 `policy`：`safe` 表示只读、纯计算或仅写入本系统提案；`ask` 表示需要人工审批后才能执行的模拟动作；`deny` 表示外部系统副作用，注册表在调用前直接拒绝（当前没有注册任何 deny 工具）。`simulate_execute_approved_action` 是唯一的 `ask` 工具，同时受 approver 角色、`approved` 条件 UPDATE 和唯一执行记录约束。审批页面在执行前展示待模拟动作的参数（SKU、参考日预算、模拟新预算），避免“只看到建议、看不到具体动作”。模型可见工具另受 `LLM_TOOL_ALLOWLIST`、`effect`、`policy` 和角色四重限制：`create_recommendation` 虽为 `safe`，但 `effect=database_write`，不会暴露给模型；审批与模拟执行工具也不在允许列表。

审计接口支持按 `object_id` 或 `request_id` 精确过滤；时间戳统一输出 UTC ISO 8601（`Z`），SQLite 与 PostgreSQL 行为一致。当前认证是可运行的自建账号体系，不是完整生产 IAM：没有 OIDC/SSO、密码重置、多因素认证、密钥轮换和租户管理后台；生产仍需要这些能力、分布式限流/共享状态和审计留存策略。React 演示工作台仍通过 `/api/demo` 的固定 demo token 切换身份；JWT 登录 API 已实现并有后端测试，但演示前端尚未接入登录页。

## 可观测性、限流与熔断

`backend/app/observability.py` 提供进程内指标注册表和固定容量令牌桶，不引入 Prometheus 客户端或 Redis 依赖。`GET /metrics` 输出 Prometheus 文本格式：HTTP 请求计数/耗时直方图（路径标签使用路由模板，避免 UUID 造成高基数）、分析任务事件与执行耗时、队列深度、工具调用、LLM 调用与熔断状态、限流命中次数。`PROFITOPS_METRICS_ENABLED=false` 时端点返回 404 且不再记录指标；`/health` 与 `/metrics` 本身不参与限流。

限流按 `scope + 客户端 IP + Bearer token 哈希` 分桶：登录默认 10 次/分钟，创建分析默认 30 次/分钟，其他 API 默认 120 次/分钟；超限返回 429 `rate_limited`、`Retry-After` 和 `X-RateLimit-Limit`，不泄露账号是否存在。桶是进程内状态，多副本部署必须换成共享存储；当前单进程 API + 进程内 worker 的演示/单机部署不跨进程共享。

LLM Provider 在 `build_llm_provider` 中被 `CircuitBreakerProvider` 包装：连续可重试错误（429、5xx、超时、网络、空响应）达到阈值后打开熔断，冷却期内直接返回 `llm_circuit_open`，不再发起网络请求；冷却后放行一个半开探测，成功关闭，失败重新打开。鉴权、余额不足和非法输出等确定性错误不计入熔断，避免掩盖配置问题。熔断错误仍走既有 `LLM_FALLBACK_POLICY`：`rules` 时确定性分析继续并标记降级，`fail` 时分析失败；状态和错误码在 `result.llm`、审计和 `/metrics` 中可见。当前没有分布式追踪、OpenTelemetry 导出或跨进程日志聚合。

## 前端

React + TypeScript + Vite + 自定义 CSS，Recharts 只负责趋势图。所有页面数据通过真实 API 获取，无静态经营假数据。关键状态包括：初始加载、空数据初始化、API 错误（带请求 ID）、分析轮询、建议审批、待执行动作预览、重复执行回放和手机布局。审计页支持按请求 ID 精确过滤；趋势图提供表格替代视图；颜色使用通过 CVD 校验的蓝/橙两色，并始终有图例与数据表。

Agent 工作台新增 LLM 面板：显示实际模型、`llm_success/deterministic_fallback/llm_failed/not_requested` 状态、耗时、尝试次数、仅在 API 真实返回时显示 Token 用量，以及摘要、置信度、根因、建议、预期影响、风险、待补充信息和全部证据引用。降级时明确标注“不是 LLM 输出”并显示原因/错误；`not_requested` 说明本次未触发 LLM。建议详情同样展示 LLM 根因分析，审计页把 `degraded` 标为“已降级”。

运营知识库页面提供检索、命中片段、分数、来源/版本和 `no_answer` 状态；approver 可新增或停用文档，analyst 只读。分析结果与建议详情展示 `knowledge` 上下文：检索 query、embedding 模型、耗时、片段列表和“✓ LLM 已引用”标记；无可靠答案时明确显示“无可靠答案”，不把低分片段包装成结论。页面注明知识内容是数据而非指令，只有通过服务端校验的 chunk_id 才能显示为引用。

## 数据初始化与迁移

首次启动自动建表并加载固定种子 `demo-60d-v1`：2026-09-01 至 2026-10-30、2 店铺、3 SKU，订单和每日广告/库存记录均由确定性规则生成。知识库另用 `knowledge-v1` 幂等加载 6 篇合成 SOP。`seed_versions` 与事务保证重复调用不重复插入，也不清除审批、审计历史或用户知识文档。

Alembic 入口位于 `backend/alembic.ini` 和 `backend/migrations/`，`0001_baseline` 用当前 SQLAlchemy metadata 建立新库；已由启动兼容迁移建表的旧库用 `scripts/migrate.py stamp 0001_baseline` 标记，不要重复 upgrade。`0002_tenant_scoped_idempotency` 把 analyses/executions 的 idempotency_key 从全局唯一、`VARCHAR(100)` 改为 `(tenant_id, idempotency_key)` 复合唯一、`VARCHAR(200)`，并保留既有行；这一步必须由 Alembic 执行，启动兼容迁移不会改这两个约束。应用启动仍执行 `initialize_database` 的兼容迁移（补充 `tenant_id`、任务字段和知识库部分唯一索引），因此现有演示库可先启动；涉及幂等约束的旧库必须运行 `python scripts/migrate.py upgrade head`。后续 schema 变更必须新增显式 revision。备份、迁移和回滚步骤见 `docs/RUNBOOK.md`。

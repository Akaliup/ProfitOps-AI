# ProfitOps AI v1 契约

## 多店铺经营智能增量契约

订单、广告和库存 CSV 接口 `/api/data/{orders|ads|inventory}/preview|commit` 采用原始 UTF-8 CSV 请求体；预览可读，提交仅 approver。`GET /api/data/template/{kind}` 返回表头。批次与错误行沿用 `/api/imports`；来源行写入新增 `source_records`，按租户、类型、自然键唯一，关联 `import_batches`。`GET /api/inventory` 返回可配置的确定性店铺/SKU 风险与补货量；`POST /api/weekly-reports` 接受已完成 UTC 自然周的周日 `end_date`、可选 `store_id`、`inventory_policy` 与 `use_llm`，`GET /api/weekly-reports` 和 `GET /api/weekly-reports/{id}` 按租户读取已保存快照。周报金额仍为整数美分，复用 `fetch_metrics` / `build_report` 八项利润桥并保留来源批次与行号。LLM 仅产出引用已知 evidence ID 的文字，数据不足、引用无效或 Provider 失败时返回确定性降级，不执行真实采购/广告/调价。详细字段、算法边界、迁移与 CSV 样例见 [INTELLIGENCE.md](INTELLIGENCE.md)。

`metrics.decomposition` 用整数美分拆销售额变化：`sales_change = volume + price + mix + residual`。它不替换八项净利润桥，也不与 `net_profit` 逐项恒等。任一侧件数为 0 时状态为 `INSUFFICIENT_DATA`，ASP 为 null，不估算价格。仅一侧有件数的 SKU 不丢弃：没有可比 ASP，其全部销售额变化进入 SKU 销量项，与组合销量效应的差额进入 mix。证据 id 为 `ev-volume`、`ev-price`、`ev-mix`、`ev-residual`，分店为 `{store_id}:ev-*`。

调价建议规则版本 `pricing-rules-v1`，只读，不执行改价。件数或净销售额不足，或可变成本合计为 0 且不能证明成本列已导入时，`suggested_price` 为 null、状态为 `INSUFFICIENT_DATA`。证据 id 为 `{store_id}:sku:{sku}:pricing`。

`GET /api/data/adapters` 与 preview/commit 的 `adapter` 参数只做离线列映射，预设为 `amazon-orders`、`shopify-orders`、`amazon-ads`，没有库存适配器，也不连接卖家后台。非法 JSON mapping、未知 adapter、kind 不匹配返回 422，整批不入账。活动诊断 id 为 `{store_id}:campaign:{campaign_id}`；`attributed_sales` 不计入利润桥。

## 周报追问（0005）

`GET /api/weekly-reports/{id}/messages` 返回当前租户唯一线程及按时间升序的消息；尚未提问时 `thread=null`、`items=[]`，不自动建线程。`POST /api/weekly-reports/{id}/messages` 只接受 `question`（1–1000 字符，去首尾空白），不信任请求体中的周报、店铺或工具参数。user 与 assistant 消息在同一事务写入；未配置模型、模型失败、引用非法或数字无法核对时仍返回 200，以 `deterministic_fallback` 或 `refused` 明确说明，不把降级伪装成模型成功。周报不存在或跨租户统一返回 404 `report_not_found`；user+assistant 合计超过 20 条返回 422 `weekly_chat_limit`；审计动作 `weekly.chat` 记录线程、消息、状态与原因。

`weekly_chat_threads` 以 `(tenant_id, report_id)` 唯一，`weekly_chat_messages` 仅允许 role 为 `user` / `assistant`，`trace` 不返回前端。已保存 `WeeklyReport.body` 不可变，聊天记录单独持久化。追问只通过只读工具 `get_saved_weekly_report` 读取当前租户的这一份周报，不调用 `weekly_data` 现场重算，也不读取其他周报。读取时可确定性派生 `{store_id}:store`、`{store_id}:sku:{sku}`（含当期与上期 `sales` / `units` / `net_profit`）、`{store_id}:sku:{sku}:pricing`、`{store_id}:campaign:{campaign_id}`，以及周报里已有的 `ev-*` / `{store_id}:ev-*`。这些派生 id 不写回历史快照。

成功 claim 的 evidence ID 必须来自本轮完整工具结果；回答中的阿拉伯数字必须能在所引证据中找到，仅已知金额字段及利润桥金额允许由整数美分换算为两位小数美元。可换算字段包括账本金额、桥金额，以及 `asp`、`suggested_price`、`current_price`、`attributed_sales`、`sales_change`。库存、件数、点击、曝光、归因订单数、天数、MOQ 等不换算。`refund_rate_pct` 与 `margin_pct` 已是百分数，接受 `15` 与 `15.0`，不除以 100。嵌套对象按叶子字段名判断，SKU、日期和证据 ID 等标识符先屏蔽再抽数字。工具结果按不可信数据标记，保留全部引用 ID、来源和必要标量，不携带订单 ID 长列表或重复公式；对比期统一放在 `metrics.previous_period`。工具结果超过 `LLM_TOOL_RESULT_MAX_BYTES` 时失败并降级，不截断证据或保留残缺引用。该工具不属于全局 `LLM_TOOL_ALLOWLIST`。MCP 只读暴露同一个 `get_saved_weekly_report`，仍按服务端租户过滤；追问路径没有采购、调价、预算或执行工具。

创建线程和初次上限检查在短事务内完成；LLM 与工具循环在事务外调用。保存前通过短事务中的线程行锁（SQLite 为 `BEGIN IMMEDIATE`）再次检查 20 条上限，原子写入问答和审计。并发填满时返回 422 `weekly_chat_limit`，不返回未保存的回答。模型调用阶段不持有写事务。

## 领域与口径

一个店铺拥有订单和每日经营记录，订单引用 SKU；每日经营记录对 (store_id, product_id, date) 唯一，包含广告费用、期末库存、在途库存、完整性标志。订单中的采购、履约、平台与其他成本均为该订单的独立可变成本，不包含广告；退款为负向收入，采购成本已经反映退货残值，不再对退款重复扣成本。所有演示金额为 USD 整数美分，无汇率和税项。

净销售额 = sales - discounts - refunds。
净利润 = net_sales - cogs - ad_spend - fulfillment - platform_fees - other_costs。
利润率 = net_profit / net_sales × 100%。净销售额为 0 时利润率为 null。
变化率 = (current - previous) / abs(previous) × 100%，previous = 0 时为 null；变化金额仍可用。利润率差使用百分点 pp。

UTC 日期区间为闭区间。当前区间长度 N，上一周期紧邻、同为 N 天。默认当前为种子数据最后 30 天。查询支持 start_date、end_date（必须同时提供，最多 90 天）、store_id。所有维度和订单聚合共享同一范围。未选店铺表示所有店铺。

空集不等于正常经营零销售；每日经营记录覆盖全部店铺 × SKU × 日期才算完整，缺行或 incomplete 标记会产生具体缺失项。金额可聚合用于展示，但证据不完整时 Agent 必须停止并标记 insufficient_data，不创建可执行建议。订单外键、金额非负和唯一约束防止不合法源数据。

## 数据模型

- tenants、users：租户与登录用户；用户以 `(tenant_id, email)` 唯一，密码只保存 scrypt 哈希，角色由服务端数据库决定。
- stores、products、orders、daily_operations：业务事实；均带 `tenant_id`，查询必须先按租户过滤；daily_operations 为每日覆盖依据。
- analyses：请求范围、状态、完整的 JSON 工作流快照、结果、步骤、错误、请求 ID、`tenant_id`、任务执行字段（`attempts`、`max_attempts`、`next_run_at`、`lease_until`、`worker_id`）；idempotency_key 按 `(tenant_id, idempotency_key)` 唯一且绑定请求内容与创建者，存储列宽 200，客户端键最多 100 字符，非 demo 租户在存储时加租户前缀，因此同一客户端键在不同租户互不占用。`result` 包含确定性 `metrics`、`findings`、`llm` 运行元数据和 `engine`。
- recommendations：每次分析至多一条建议；证据、影响区间、假设、风险、模拟动作参数、审批信息。`body.llm` 保存本次 LLM 运行结果（成功时含结构化输出），`body.knowledge` 保存检索片段及 `cited` 标记。批准时写入 `body.approval` 版本快照：`version`、`captured_at`、`scope`、`ledger_hash`、`action_hash`、动作参数副本、当前/上一周期合计、数据质量与 SKU 明细。快照是审批约束，不是可重算的展示字段。
- knowledge_documents、knowledge_chunks：运营知识文档与 Markdown 分段；文档带 `tenant_id`，正文 SHA-256 用于创建幂等，文档软删除，片段保存 embedding 与模型名；`heading` 最长 200 字符。`knowledge_documents(tenant_id, content_sha256)` 上有部分唯一索引（仅 `active=true`），保证同一租户同一正文最多一个 active 文档；不同租户可以各自拥有相同正文，历史软删除行可保留任意数量。启动时对旧库先按 `(tenant_id, created_at, id)` 保留最早 active 行、其余软删除，再创建索引；不删除任何行。
- executions：recommendation_id 唯一，idempotency_key 按 `(tenant_id, idempotency_key)` 唯一（列宽 200，客户端键最多 100 字符）；带 `tenant_id`，模拟结果和错误诊断。
- audit_logs：`tenant_id`、操作人、动作、对象、时间、请求 ID、结果与详情；列表按租户过滤。
- import_batches：每次成功提交的 CSV 批次，记录 `kind`（orders / operations）、文件名、正文 SHA-256、写入/跳过行数、金额合计、操作人与请求 ID。批次只追加，不修改订单或每日经营事实。
- schema_versions、seed_versions：可重复初始化版本记录。

## 身份与状态机

认证有两种方式：demo mode 下固定 Bearer token（demo-analyst → analyst，demo-approver → approver，均属于 demo 租户），以及 `POST /api/auth/login` 用邮箱密码换取 HS256 JWT。JWT 包含 `sub`、`tenant_id`、`iss`、`iat`、`exp`，服务端校验签名、签发方、过期时间，并从数据库重新读取用户、角色和租户状态；不信任 token 内的角色声明。客户端传 X-Role 或 actor 无效。非 demo mode 必须配置至少 16 字符的 `AUTH_SECRET`，演示 token 不再生效。所有租户数据查询都带 `tenant_id` 条件，跨租户对象按不存在处理（404），不返回资源是否存在。

建议：proposed → pending_approval → approved → executing → executed；pending_approval → rejected；executing → execution_failed。审批结果不可反转。只有 approver 可以审批、拒绝和模拟执行。analyst 可发起分析、读指标和审计。

条件 UPDATE 抢占审批或执行；执行在单个数据库事务中将 approved 变为 executing、插入唯一执行记录、模拟修改并存结果，最终 executed / execution_failed。等待竞争事务后重复调用返回同一 execution 且 replayed=true，不产生第二条模拟变更。不同建议不能复用执行 Idempotency-Key。拒绝的请求和失败同样审计。

审批与执行之间的版本保护：批准前重新读取当前范围账本，若数据质量不完整或当前证据与建议中的证据不一致，返回 409 `recommendation_stale`，建议保持 `pending_approval`。批准成功后，账本原始行指纹和动作参数指纹写入 `body.approval`。执行时先完成 approved→executing 的条件抢占，再核对快照：快照缺失返回 409 `approval_snapshot_missing`，动作指纹不一致返回 409 `action_changed`，审批后订单或每日经营记录任意变化返回 409 `approval_stale`。失败时整个事务回滚，不创建 Execution，建议保持 `approved`，需重新分析并审批。已存在 Execution 的幂等重放优先返回原记录，不因之后账本变化而失败。审计动作分别为 `recommendation.stale`、`execution.stale`、`execution.action_changed`。

LangGraph 分析图（异常路径）：receive_request → get_sales_metrics → calculate_profit → analyze_profit_drivers → verify_evidence → retrieve_knowledge → llm_root_cause_analysis → verify_llm_evidence → create_recommendation → wait_for_approval → END。无异常或数据不完整时从 verify_evidence 直接进入 wait_for_approval，不检索知识库，也不调用 LLM。每个节点将状态快照提交到数据库；等待为持久化业务暂停点。审批后从数据库加载同一快照进入 LangGraph 执行图：get_approval_status → simulate_execute_approved_action → save_execution_result。不使用仅内存 checkpoint 冒充可恢复性。读快照不重跑已经完成的建议生成。节点超时预算在每次步骤前后检查，失败记录 failed/timeout；不强行终止 Python 阻塞线程。LLM 节点使用 Provider 自身的 `LLM_TIMEOUT` 与有界重试，不叠加步骤超时。分析是持久化任务：POST 先写入 queued 行，worker 用条件 UPDATE 抢占（status=queued → running）并写入 attempts、worker_id 和 lease_until；工作流执行期间心跳续租。`PROFITOPS_ANALYSIS_WORKER_ENABLED=false` 同时关闭 API 进程内 worker 线程和 POST/retry 的 BackgroundTasks，任务保持 queued，直到 `python -m app.worker`（或 `--once`）在同一个数据库上消费；两种执行路径共用同一套条件 UPDATE、租约与重试规则。租约过期的 running 行在启动或轮询时重新排队（lease_expired），不会因为进程重启永久卡住；已用完 `max_attempts` 的过期租约行保持 failed 并标记 `dead_letter`，不会无限重新入队。worker 的每次写入——创建建议、持久化步骤/结果（`write_job`）、写审计前的所有权确认（`assert_owned`）以及心跳续租（`LeaseHeartbeat`）——都在同一条条件 UPDATE 中校验 `status='running'`、`worker_id`、`attempts` 和 `lease_until > 当前时间`。时间比较在数据库侧执行（SQLite 返回无时区值，Python 侧比较会出错或与存储值不一致）；`lease_until == now` 视为已到期，与恢复扫描的 `lease_until <= now` 一致。租约到期后，即使尚未被新 worker 接管，旧 worker 也不能创建建议、持久化新结果或延长租约；心跳发现租约丢失后停止。已被回收的旧 worker 写入会回滚并停止工作流。`workflow_failed`、`step_timeout`、`database_locked`、`worker_crashed` 按指数退避重试，达到 `max_attempts` 后保持 failed 并标记 `dead_letter`；`source_failed`、`llm_failed` 等确定性失败不自动重试。失败行可由认证用户调用 retry 接口手动重新排队。`run_analysis` 已写入 failed 但进程在 `finalize_job` 之前退出时，该行仍保留 `worker_id` 与过期租约；启动或轮询的恢复扫描会用同一重试/死信规则处理这些行，并在错误详情标记 `recovered_before_finalize=true`，不会永久卡在 failed。

## LLM 分析与降级

配置来自后端进程环境（项目根 `.env` 或真实环境变量）：`LLM_PROVIDER`、`LLM_MODEL`（默认 `deepseek-flash`）、`LLM_API_KEY`、`LLM_BASE_URL`（默认 `https://api.deepseek.com`）、`LLM_TIMEOUT`（默认 20s）、`LLM_MAX_TOKENS`（默认 4000）、`LLM_MAX_RETRIES`（默认 1）、`LLM_FALLBACK_POLICY`（`rules` 默认 / `fail`）、`LLM_NATIVE_TOOLS`（默认 true）、`LLM_TOOL_MAX_ROUNDS`（2–8，默认 4）、`LLM_TOOL_MAX_CALLS`（1–12，默认 6）、`LLM_TOOL_MAX_REPEATS`（1–5，默认 2）、`LLM_TOOL_BUDGET_SECONDS`（默认 90）、`LLM_TOOL_RESULT_MAX_BYTES`（512–65536，默认 16384）。`LLM_API_KEY` 使用 SecretStr，API 不返回密钥。未配置 Key 时 `llm_configured=false`，分析仍完成。

LLM 在确定性利润计算、异常归因和证据校验之后运行，输入为已计算的周期、利润桥、主要驱动、8 项证据、SKU 摘要和已规划模拟动作；金额字段为整数美分，输入显式携带 `amount_unit`。启用 `LLM_NATIVE_TOOLS` 时使用原生 Function Calling 的有界循环：请求携带 `tools`，普通轮 `tool_choice=auto`，最后一轮强制 `none`；模型返回的 `tool_calls` 经 JSON、显式允许列表、Registry 存在性、`policy=safe`、`effect=read|pure`、服务端角色、Pydantic 输入 Schema 和本次分析范围（带 period 的工具其 period/store_id 必须完全一致；`search_knowledge` 无 period，不做范围比较）逐项校验后，才调用服务端只读工具，结果以 `role=tool`、`tool_call_id` 回传，再进入下一轮模型调用。允许模型调用的工具只有 `get_profit_analysis`、`get_sku_breakdown` 与 `search_knowledge`；模型不能调用 `create_recommendation`、审批、执行或其他写工具。任何被拒绝或执行失败的工具调用都会终止循环，按降级策略处理，不会继续尝试写操作。

循环受 `LLM_TOOL_MAX_ROUNDS`、`LLM_TOOL_MAX_CALLS`、相同工具+参数重复次数、总预算 `LLM_TOOL_BUDGET_SECONDS` 和单次结果 `LLM_TOOL_RESULT_MAX_BYTES` 限制；终止原因包括 `final_answer`、`llm_tool_round_limit_exceeded`、`llm_tool_call_limit_exceeded`、`llm_tool_duplicate_call_id`、`llm_tool_repeated_call`、`llm_tool_budget_exceeded`、`llm_tool_scope_mismatch`、`llm_tool_forbidden`、`llm_tool_unknown`、`llm_tool_invalid_arguments`、`llm_tool_execution_failed`、`llm_tool_output_truncated` 和 Provider 错误码。`result.llm` 记录 `native_tools`、`termination_reason`、逐轮 `rounds[{round,finish_reason,tool_call_count,latency_ms,usage}]` 和逐次 `tool_calls[{round,tool_call_id,name,arguments,ok,result_summary,error_code,latency_ms}]`；失败时保留已完成部分。工作台“Agent 执行轨迹”只消费这些字段，不重新实现工具循环：显示轮次、模型、`finish_reason`、工具名、参数摘要、`ok`/失败、错误码、单轮 token 与耗时、总 token/总耗时和终止原因；`ok=false` 必须显示为失败，参数与结果只显示有界摘要，不显示 API Key、认证头或原始请求头。工具参数摘要只展示经过白名单校验的 `period`（ISO 日期）和 `store_id`（受限字符集）；其他字段只显示数量，非法值显示为“已隐藏”，不得对原始参数对象做 JSON 序列化回显。不支持 `chat()` 的 Provider（测试与评测 FakeProvider）继续走单次 `generate_json` 路径，`native_tools=false`。

最终输出必须符合 `LLMAnalysisOutput`：`summary`、`root_causes[{cause,reasoning,evidence_ids}]`、`evidence_references`、`knowledge_references`（最多 8 个 chunk_id）、`recommendations[{action,rationale,evidence_ids,priority}]`、`expected_impact{direction,horizon_days,explanation}`、`risks`、`missing_information`、`confidence`（0–1）。所有证据 ID 必须是本次 `findings.evidence` 的子集；`knowledge_references` 必须是本次预检索与工具检索合并后 `hits` 的 chunk_id 子集。`verify_llm_evidence` 会遍历全部引用，发现未知证据 ID 或未知知识 chunk_id 即判定无效，不进入审批流。`finish_reason=length` 导致截断时错误码为 `llm_tool_output_truncated`（原生路径）或 `llm_invalid_json`（单次路径），消息与详情标明截断。LLM 不返回也不覆盖任何利润、成本、销售额数字；动作参数、预期影响金额仍由确定性 `plan_action` 计算。

`result.llm.status` 取值：

- `llm_success`：真实调用成功且 Schema/证据校验通过；`output` 非空，`model`、`latency_ms`、`attempts`、`usage` 来自实际响应；原生路径 `native_tools=true` 且 `termination_reason=final_answer`，并带 `rounds`/`tool_calls` 轨迹。
- `deterministic_fallback`：未配置 Key（`reason=not_configured`）或调用失败/证据引用无效（带 `error`）；本次建议使用确定性文案，`output=null`。
- `llm_failed`：仅当 `LLM_FALLBACK_POLICY=fail`；分析状态 `failed`，错误码 `llm_failed`，运行元数据仍写入 `result.llm`。
- `not_requested`：无异常（`reason=no_anomaly`）或数据不完整（`reason=insufficient_data`），LLM 未被触发。

`engine` 仅在 `llm_success` 时为 `deepseek-llm-v1`，否则为 `deterministic-rules-v1`。审计动作：`analysis.llm_succeeded`、`analysis.llm_fallback`（outcome=degraded）、`analysis.llm_failed`。Provider 错误码包括 `llm_auth_failed`、`llm_insufficient_balance`、`llm_rate_limited`、`llm_server_error`、`llm_http_error`、`llm_timeout`、`llm_network_error`、`llm_invalid_json`、`llm_empty_response`、`llm_schema_invalid`、`llm_invalid_tool_call`、`llm_circuit_open`、`invalid_evidence_reference`；原生工具循环另有上文列出的 `llm_tool_*` 错误码。

Provider 外层由 `CircuitBreakerProvider` 计量并保护：连续可重试错误达到 `PROFITOPS_CIRCUIT_BREAKER_FAILURES`（默认 3）后打开熔断，`PROFITOPS_CIRCUIT_BREAKER_COOLDOWN_SECONDS`（默认 30）内直接返回 `llm_circuit_open`，冷却后放行一个半开探测。鉴权、余额不足、非法输出等确定性错误不计入熔断。熔断状态通过 `/metrics` 的 `profitops_llm_circuit_state`（0 closed、1 half-open、2 open）暴露；熔断期间仍按 `LLM_FALLBACK_POLICY` 降级或失败，不会产生真实外部副作用。

## 知识库检索（RAG）

知识库是服务端运营 SOP 的只读检索层，不是模型可任意写入的记忆。`knowledge_documents` 保存标题、来源、版本、生效日期、正文、SHA-256、元数据与 `active`；`knowledge_chunks` 保存按 Markdown 标题切分的片段、字符数、embedding 向量和 `embedding_model`。删除为软删除（`active=false`），保留审计与历史；创建时相同正文 SHA-256 幂等返回已有文档（`created=false`），不重复入库；并发上传由数据库部分唯一索引与事务保证最多一个 active 文档（SQLite 先取写锁，PostgreSQL 用唯一冲突 + savepoint 恢复）。相同正文但当前 embedding model 或向量维度与现有 chunk 不同时，会在原文档上重嵌入现有 chunk（保留文档 ID、source、version、effective_date、active 与 chunk ID/正文），返回 `created=false, reindexed=true`；显式 reindex 可重复执行，已是最新模型与维度时为 no-op。`knowledge-v1` 种子包含 6 篇合成 SOP，重复 Seed 不重复插入，也不删除用户文档。

默认 `KNOWLEDGE_EMBEDDING_PROVIDER=hashing`：确定性特征哈希向量（默认 256 维），不联网、无额外依赖，测试与 CI 可复现。`openai` 走任意 OpenAI 兼容 `POST {base_url}/embeddings`，要求显式配置 base URL，密钥使用 SecretStr；响应按 `index` 排序并 L2 归一化。入库前严格拒绝 `null`、非数字、`NaN`、`Infinity`、空向量、重复/缺失 `index` 和维度不一致，所有异常映射 `knowledge_embedding_failed`（502），不会写入非法向量，也不会静默换模型。启动时远程 embedding 不可用不会阻断核心 API：知识库 Seed 事务回滚，失败原因写入 `/health` 与 `/api/demo` 的 `knowledge.seed_error`；数据库初始化失败仍会中止启动。检索只使用与当前 provider model 相同、且向量维度与当前 query 向量一致的片段；换模型或同模型换维度后旧向量不参与检索，需要对已有文档执行 reindex（重嵌入现有 chunk，保留文档与 chunk ID/正文）；重复 reindex 幂等。Markdown 标题写入 `knowledge_chunks.heading` 前截断到 200 字符（与字段长度一致），正文与段落顺序不受影响。

切分按 Markdown 标题分段，超长段按字符边界切分并保留 overlap；默认 600 字符、100 重叠。检索对候选片段计算 `0.65 * cosine + 0.35 * 词法匹配 + 0.05 * 标题命中`，取 `candidate_k` 后加 `0.03 * 文档标题命中` 重排，返回 `top_k`。词法匹配对中文使用单字/双字/三字 token，单字权重较低；最高分低于 `KNOWLEDGE_MIN_SCORE`（默认 0.15）时判定 `no_answer=true` 并清空 `hits`，低置信度片段不能被引用。该阈值是离线评测校准的工程默认值，不是通用准确率保证。

分析工作流在 `verify_evidence` 之后、LLM 之前执行 `retrieve_knowledge`；查询由确定性指标与主要驱动构造。检索结果写入 `analysis.result.knowledge`，包含 `query`、`hits`、`no_answer`、`embedding_model`、`retrieval_ms`。LLM 输出可返回 `knowledge_references`，`verify_llm_evidence` 校验这些 chunk_id 必须属于**本次实际提供给模型的集合**（预检索 payload + 工具结果中可见的 ID），而不是仅属于持久化面板；未知 ID 触发 `invalid_knowledge_reference`，按 `LLM_FALLBACK_POLICY` 降级或失败。合并面板最多 10 条，但会优先保留被引用的 chunk，再按分数填充，避免低分有效引用被截断。无 LLM 时仍保留检索结果，但所有 `cited=false`。

`search_knowledge` 是第 9 个注册工具，也是第 3 个模型可见只读工具。模型可以主动补充检索；知识内容在提示词中被标记为不可信数据而不是指令，单条片段截断到 700 字符。工具返回的 hits 与预检索结果按 chunk_id 去重合并，合并面板最多 10 条并优先保留被 LLM 引用的 chunk；工具结果被字节上限截断时，只有 preview 中仍可见的 chunk_id 可被引用。知识库内容不能触发工具调用、审批或执行，也不能改变金额。知识文档的创建和停用仅限 approver，读取与检索对 analyst/approver 开放；创建/停用写入审计。

配置来自后端进程环境：`KNOWLEDGE_EMBEDDING_PROVIDER`（`hashing` 默认 / `openai`）、`KNOWLEDGE_EMBEDDING_MODEL`（默认 `hashing-v1`）、`KNOWLEDGE_EMBEDDING_BASE_URL`、`KNOWLEDGE_EMBEDDING_API_KEY`、`KNOWLEDGE_EMBEDDING_DIMENSIONS`（32–1024，默认 256）、`KNOWLEDGE_CHUNK_CHARS`（200–2000，默认 600）、`KNOWLEDGE_CHUNK_OVERLAP`（0–500，默认 100，必须小于 chunk chars）、`KNOWLEDGE_TOP_K`（1–10，默认 5）、`KNOWLEDGE_CANDIDATE_K`（1–100，默认 20，不小于 top_k）、`KNOWLEDGE_MIN_SCORE`（0–1，默认 0.15）。

## MCP（只读，stdio）

`backend/app/mcp_server.py` 是独立的 stdio MCP Server，复用同一 Tool Registry，只暴露 `get_saved_weekly_report`、`get_weekly_intelligence`、`get_inventory_risks`、`get_profit_analysis`、`get_sku_breakdown`、`search_knowledge`、`get_approval_status` 7 个 `effect=read|pure`、`policy=safe` 的只读工具。`create_recommendation`（database_write）和 `simulate_execute_approved_action`（ask）不在暴露列表，调用返回 `unknown_tool`；MCP 不提供审批、拒绝、执行或用户/租户管理。角色由服务端 `PROFITOPS_MCP_ROLE`（`analyst` 默认 / `approver`）决定，不接受客户端角色头或工具参数中的 `role`。MCP 进程不建表、不 Seed，数据库必须先由主服务或初始化流程准备好；工具调用不写业务表，也不构建 LLM Provider。`search_knowledge` 与 HTTP/LangGraph 共用知识库和 `chunk_id`，返回的正文是不可信数据而非指令；默认 hashing embedding 不联网，只有显式配置远程 embedding 时才访问该网关。MCP 不改变 HTTP 审批状态机，所有审批与模拟执行仍必须通过 HTTP + 人工 approver 完成。

## 数据导入与对账（CSV）

导入是经营事实的唯一人工入口：`orders` 写订单明细，`operations` 写每日经营记录。请求体是原始 CSV（`Content-Type: text/csv`）；文件名优先使用 `?filename=` 查询参数（URL 编码，支持中文），也接受百分号编码的 `X-Filename` 头作为回退，缺省为 `upload.csv`。文件必须是 UTF-8（接受 BOM），单次不超过 5 MB、5000 行、100 个错误明细。

列定义固定且区分大小写不敏感：

- orders：`order_id,store_id,sku,date,quantity,sales,discounts,refunds,cogs,fulfillment,platform_fees,other_costs`
- operations：`store_id,sku,date,ad_spend,inventory,inbound_inventory,complete`

金额列为十进制美元字符串，最多两位小数，服务端用 `Decimal` 精确转换为整数美分，不接受浮点、科学计数法或超过两位的小数；单值绝对值上限 1000 亿美元分。`date` 为 `YYYY-MM-DD`，范围 2000-01-01 至今天（UTC）。`quantity` 为 1–1,000,000 的整数；库存为非负整数；`complete` 接受 `true/false` 或 `1/0`。订单额外要求 `discounts + refunds <= sales`。

自然键为 orders 的 `order_id` 与 operations 的 `(store_id, product_id, date)`。同一文件内重复自然键直接拒绝；与账本已存在的行逐字段比较，完全一致则跳过（重复上传是 no-op，不产生第二条记录），任一字段不同则作为冲突拒绝。导入不覆盖、不删除已有事实，冲突必须由人处理。`store_id` 必须属于当前租户，`sku` 必须已存在于主数据；导入不隐式创建店铺或 SKU。

整批原子：任一行未通过校验，整批不写入任何业务行，返回 409 `import_rejected` 与逐行问题摘要。校验项包括表头完整、未知店铺/SKU、日期范围、金额格式与范围、折扣退款上限、整数范围、文件内重复与账本冲突。预览与提交使用同一套校验函数，预览不写库。

对账分两层：先在内存中按整数美分逐行加总销售额、净销售额、各项成本与贡献利润；写入并 flush 后再按主键分批回读 `count` 与 `sum`，与文件合计逐项比较。回读不一致时抛出 `import_reconciliation_failed` 并回滚整个事务，不会留下半批数据。成功响应包含 `checks`（`parse_complete`、`money_exact`、`row_balance`、`duplicate_policy`、`db_roundtrip`）及每项结果。

权限：analyst 与 approver 都可以预览；只有 approver 可以提交。成功与拒绝分别写审计 `import.committed`、`import.rejected`，不记录完整文件内容，只记录文件名、SHA-256、行数与金额合计。批次列表按租户隔离，跨租户返回 404。

## API

全部 JSON；`/health` 与 `/metrics` 无认证，`/api/demo` 可匿名读取 demo 租户概览（带 Bearer 时返回该租户自己的店铺与数据范围），其余 API 需 Bearer。`POST /api/auth/login` 返回 `{access_token,token_type,expires_in,user}`；`GET /api/auth/me` 返回当前用户、角色与租户。金额字段为整数美分，currency=USD。错误：`{error:{code,message,details},request_id}`；响应含 X-Request-ID。登录、创建分析和其他 API 分别按 IP + token 哈希限流，超限返回 429 `rate_limited` 与 `Retry-After`；`/health`、`/metrics` 不限流。限流身份只接受 demo token 或签名有效的 JWT；缺失、伪造或过期 Bearer 共用匿名桶，轮换无效 token 不能绕过基础限额。TokenBucketLimiter 的 bucket 数量是硬上限，达到上限时先清理一小时未更新的桶，再按最旧时间淘汰，唯一 key 突发不会突破 `max_buckets`。

- GET /health：数据库连通性、simulation 标志、`llm.{configured,provider,model}` 与 `knowledge.{provider,model,remote,seed_error}`（不含密钥）；`seed_error` 为 `null` 或 `{code,message}`。
- GET /metrics：Prometheus 文本格式，包含 HTTP 请求计数/耗时、分析任务事件与耗时、队列深度、工具调用、LLM 调用/熔断状态和限流命中；`PROFITOPS_METRICS_ENABLED=false` 时返回 404。HTTP 指标标签使用路由模板（如 `/api/analyses/{analysis_id}`）；未匹配路由固定为 `<unmatched>`，限流器在路由前提前返回的 429 固定为 `<rate_limited>`，任意请求路径都不能制造无界标签。
- GET /api/demo：demo mode、演示身份、可用店铺和数据日期（可空）、`llm.{configured,provider,model,fallback_policy}`，以及 `knowledge.{documents,embedding_provider,embedding_model,seed_error}`。
- POST /api/demo/seed：analyst 可重复初始化，不清除历史；同时幂等初始化 `knowledge-v1` 知识库。
- GET /api/dashboard/summary：period、previous_period、current、previous、changes、trend、drivers、sku_breakdown、data_quality、anomaly。
- GET /api/metrics：同一指标契约（支持范围/店铺）。
- POST /api/analyses：`{start_date?,end_date?,store_id?}`，可选 Idempotency-Key；202 返回 queued 分析，任务被 worker 抢占后执行有状态图。
- GET /api/analyses、GET /api/analyses/{id}：执行步骤、工具摘要、证据、错误、`result.engine`、`result.llm`、`result.knowledge`，以及 `attempts`、`max_attempts`、`next_run_at`；原生路径的 `result.llm` 另含 `native_tools`、`termination_reason`、`rounds` 和 `tool_calls`。检索失败时保留 `result.knowledge_error`，不阻断确定性分析。GET 列表支持 `status` 过滤。
- POST /api/analyses/{id}/retry：仅 failed 分析可重试；用单条条件 UPDATE（`status='failed'`）原子地重置为 queued、清除租约并增加一次允许尝试，返回 202。并发重试或重试与 worker 交错时只有一个请求成功，其余返回 409 `analysis_not_retryable`；running/completed 不会被改回 queued。
- GET /api/tools：工具输入输出 JSON Schema、权限和副作用分类。
- GET /api/imports/template/{orders|operations}：下载 UTF-8 CSV 模板。
- GET /api/imports：当前租户的导入批次，最新在前，`limit` 1..200，默认 50。
- GET /api/imports/{id}：单个批次；跨租户按不存在处理（404）。
- POST /api/imports/{orders|operations}/preview：原始 CSV，返回逐行问题、合计与校验项；不写业务表。
- POST /api/imports/{orders|operations}：仅 approver；校验通过后单事务写入并回读对账，返回批次 ID、`status=committed|noop` 与 `db_roundtrip` 结果；任一拒绝行返回 409 且不写入。
- GET /api/knowledge/documents：当前 active 知识文档及 chunk 数。
- POST /api/knowledge/documents：仅 approver；`{title,source,version,effective_date?,content,metadata?}`；新建返回 201，相同正文幂等返回已有文档 200（`created=false`），并发重复上传也返回同一文档；若当前 embedding model 或向量维度不同则重嵌入并返回 `reindexed=true`。
- POST /api/knowledge/documents/{id}/reindex：仅 approver；用当前 embedding provider 重嵌入该文档现有 chunk，保留文档 ID、source、version、effective_date、active 与 chunk ID/正文；返回 200，已是最新模型与维度时 `reindexed=false`；文档不存在或已停用返回 404。
- DELETE /api/knowledge/documents/{id}：仅 approver；软删除，保留片段与审计。
- POST /api/knowledge/search：`{query,top_k?}`；返回 `{query,hits,no_answer,embedding_model,retrieval_ms}`，`hits[{chunk_id,document_id,title,source,version,heading,content,score,rank}]`。
- GET /api/recommendations、GET /api/recommendations/{id}：建议及 simulation 动作与执行记录；`body.knowledge` 保存本次检索片段和 LLM 是否引用（`cited`）。
- POST /api/recommendations/{id}/approve 或 /reject：`{reason}`，仅 pending_approval；409 表示状态冲突。批准时重新校验证据并写入 `body.approval`，分析后账本变化返回 409 `recommendation_stale`。
- POST /api/recommendations/{id}/execute：必须 Idempotency-Key；批准后执行，仅模拟。执行前校验审批快照，错误码为 `approval_snapshot_missing`、`action_changed`、`approval_stale`；已知执行记录的重放仍返回同一 execution。
- GET /api/audit-logs：最新在前，limit 1..200；支持 object_id 与 request_id 精确过滤（request_id 最长 64 字符）。
- GET /api/ops/summary：仅 approver；`window_hours` 1..168，默认 24。按 JWT 租户聚合持久化的 `analyses`、`recommendations`、`executions` 和 `audit_logs`：任务状态（queued/running/waiting_approval/completed/insufficient_data/failed/approved/rejected/executed/execution_failed，`jobs.by_status` 始终包含全部状态且各项之和等于 `jobs.total`）、死信、重试、错误码、LLM 状态、工具调用/失败/轮次、token 用量、按版本化单价估算的成本、审批/执行当前状态和最近失败任务。任务总数、状态分布和重试次数由 SQL 聚合；失败明细与 LLM 审计按必要字段分批读取，最近失败最多 10 条，不加载窗口内全部 Analysis ORM 对象，也不静默截断统计窗口。LLM 历史以持久化审计事件 `analysis.llm_succeeded`、`analysis.llm_fallback`、`analysis.llm_failed` 为准；同一分析没有窗口内 LLM 审计时才回退读取 `result.llm`，避免同一运行从两处重复计数。`verify_llm_evidence` 对同一次成功调用随后写入的降级/失败事件按调用特征合并为一次，保留最终状态和实际 Usage。`not_requested`、`configured=false`（含 `reason=not_configured`）以及无模型调用的记录计入 `llm.runs`/`llm.no_model_call_runs`，但不进入 token 和成本分母。`llm.model_calls` 是逻辑模型调用数：native Function Calling 每轮 Provider 请求计一次，非 native 每次逻辑调用计一次；`llm.provider_requests` 是包含 Provider 内部重试的实际 HTTP 请求数，`llm.provider_retries` 为实际请求数减逻辑调用数。成本按每次逻辑调用（native 为每轮）的 Usage 独立估算；native 多轮只有各轮都有完整 Usage 且模型有版本化单价时才给出 `complete`，任一轮缺失返回 `partial` 且 `amount_usd=null`，不得用一个成功轮次的 Usage 代表整次运行。`cost.priced_calls`/`unpriced_calls` 按逻辑调用计数，`priced_runs`/`unpriced_runs` 按运行计数（任一轮缺失则该运行未定价）；已知的可定价小计只放在独立字段 `known_subtotal_usd`，不得冒充总额。无实际调用时返回 `no_data`。不返回原始提示词、工具参数或 API Key；进程内 Prometheus 指标不作为持久历史。analyst 返回 403，跨租户记录不可见。

运行保护配置：`PROFITOPS_METRICS_ENABLED`、`PROFITOPS_RATE_LIMIT_ENABLED`、`PROFITOPS_RATE_LIMIT_REQUESTS`（默认 120/窗口）、`PROFITOPS_RATE_LIMIT_WINDOW_SECONDS`（默认 60）、`PROFITOPS_RATE_LIMIT_LOGIN_REQUESTS`（默认 10/窗口）、`PROFITOPS_RATE_LIMIT_ANALYSIS_REQUESTS`（默认 30/窗口）、`PROFITOPS_CIRCUIT_BREAKER_ENABLED`、`PROFITOPS_CIRCUIT_BREAKER_FAILURES`、`PROFITOPS_CIRCUIT_BREAKER_COOLDOWN_SECONDS`。限流桶是进程内状态，多副本部署需要共享限流存储；当前单进程部署不共享。

时间戳字段统一为 UTC ISO 8601 并带 `Z` 后缀；SQLite 与 PostgreSQL 输出一致，前端按 UTC 解析后本地化显示。

工具统一返回 `{ok,data,error}`，Pydantic 校验输入与输出；权限在 Tool Registry 与业务处理器双层验证。当前注册 9 个工具；`get_profit_analysis`、`get_sku_breakdown` 与 `search_knowledge` 是面向模型的原生只读工具。前两者输入只有 `MetricsInput`（period/store_id），由服务端用可信账本调用既有 `fetch_metrics`/`build_report`/`analyze_drivers` 计算，模型不能提交 `RawMetrics` 或完整报告；`search_knowledge` 只接受 `{query,top_k}`，无 period 范围要求，返回可引用的 chunk_id。每个工具在 `/api/tools` 声明 `policy`：`safe`（只读、纯计算或仅写本系统提案）、`ask`（必须经人工审批后执行的模拟动作）、`deny`（外部系统副作用，注册表直接拒绝，当前未注册）。`ask` 工具由审批状态机与 approver 角色双重约束。模型可见工具另受 `LLM_TOOL_ALLOWLIST`、`effect`、`policy` 和角色四重限制；`create_recommendation` 虽为 `safe`，但 `effect=database_write`，不会暴露给模型。工具参数失败为 invalid_tool_input，执行失败保留代码和消息。

## 验收

pytest 覆盖利润边界、环比、归因加总、缺失数据、LangGraph 路径与失败、Schema、身份伪造、审批冲突、并发幂等、审计过滤、工具策略、UTC 时间戳与 Seed 保留历史。LLM 测试覆盖 Provider 成功/重试/鉴权/超时/非法 JSON/空响应、环境变量读取、结构化 Schema、证据引用校验、无 Key 降级、调用失败降级、Schema 无效降级、`fail` 策略以及 LLM 成功后的审批与幂等执行；原生 Function Calling 测试覆盖请求携带 `tools`/`tool_choice`、`tool_calls` 解析、多轮工具调用、禁止写工具/未知工具/非法参数/越界范围、轮次/次数/重复/预算上限、Provider 超时与工具异常降级、结果截断和工作流轨迹持久化。全部使用 MockTransport/FakeProvider，不消耗真实 Token；原生 Function Calling 另执行了 3 次真实 `deepseek-flash` 脚本运行，第 3 次完整成功（2 个只读工具调用、`final_answer`、`all_references_valid=true`），第 1 次网络错误降级，见 `docs/LLM_LIVE_CHECK.md`。独立 JSON 样本验证业务结论。Vitest + RTL 覆盖启动、轮询、审批、动作预览、执行、审计过滤、API 错误、LLM 成功/降级面板，以及原生轨迹的正常轮次、工具参数摘要、逐轮 token、失败调用错误码和单次 JSON 路径说明；生产构建和实际 HTTP + 浏览器演示闭环。Phase 2D 原生评测测试覆盖 fixture 到临时账本的可复现性、正常多轮、多余工具、未知/写/审批工具、非法参数、越界范围、重复调用、轮次上限、工具执行失败、非法最终 JSON、非法证据、数据不完整跳过、真实入口授权/预算/未配置检查，以及 CLI 失败退出码；全部使用 `FakeToolProvider`，不消耗真实 Token。PostgreSQL 已在本机 Docker Desktop 完成容器验收；其他环境若无法运行 Docker，需明确记录验证缺口。真实 DeepSeek 单次 JSON 调用已通过 `scripts/llm_check.py` 完成一次：`deepseek-flash`、15467.974 ms、4801 tokens、`all_references_valid=true`，详见 `docs/LLM_LIVE_CHECK.md`。知识库测试覆盖 Seed 幂等与不删用户文档、相关/无关查询、`no_answer` 清空 hits、approver 创建/停用、analyst 403、重复正文幂等、引用子集校验、提示词隔离、Markdown 切分边界、OpenAI 兼容 embedding 排序/归一化与失败映射。离线 RAG 评测 `backend/evals/rag.py` 在 6 篇文档、6 个可回答与 2 个不可回答查询上得到 hit@1 83.33%、hit@3 100%、no-answer 100%、citation validity 100%，网络调用 0 次；这是合成知识库上的检索门禁，不是通用语义准确率。多租户幂等回归覆盖 Analysis 与 Execution 的跨租户同键、105 字符租户前缀键、同租户重复键拒绝；迁移测试用真实的前租户 schema（无 `tenants`/`users` 表、无 `tenant_id`、无任务字段、知识库为全局部分唯一索引）构造 SQLite 与 PostgreSQL 库，按官方路径 `stamp 0001_baseline → upgrade head` 原地升级：Analysis/Recommendation/Execution/Audit 行数与外键关系保留，列宽 200、复合唯一约束与租约字段到位，跨租户同键可用、同租户重复键拒绝，重复 upgrade 为 no-op；SQLite 迁移期间关闭外键强制并在结束后通过 `PRAGMA foreign_key_check`，重复活跃文档会保留最新一行并软删除其余行；含跨租户同键的库执行 downgrade 会因唯一约束失败并保留原行；手工重试覆盖 running/completed 不被覆盖与并发重试只成功一次；恢复测试覆盖 `run_analysis` 写入 failed 后、`finalize_job` 之前退出的行按重试/死信规则恢复。

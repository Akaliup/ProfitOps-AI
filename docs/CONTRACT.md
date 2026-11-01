# ProfitOps AI v1 契约

## 领域与口径

一个店铺拥有订单和每日经营记录，订单引用 SKU；每日经营记录对 (store_id, product_id, date) 唯一，包含广告费用、期末库存、在途库存、完整性标志。订单中的采购、履约、平台与其他成本均为该订单的独立可变成本，不包含广告；退款为负向收入，采购成本已经反映退货残值，不再对退款重复扣成本。所有演示金额为 USD 整数美分，无汇率和税项。

净销售额 = sales - discounts - refunds。
净利润 = net_sales - cogs - ad_spend - fulfillment - platform_fees - other_costs。
利润率 = net_profit / net_sales × 100%。净销售额为 0 时利润率为 null。
变化率 = (current - previous) / abs(previous) × 100%，previous = 0 时为 null；变化金额仍可用。利润率差使用百分点 pp。

UTC 日期区间为闭区间。当前区间长度 N，上一周期紧邻、同为 N 天。默认当前为种子数据最后 30 天。查询支持 start_date、end_date（必须同时提供，最多 90 天）、store_id。所有维度和订单聚合共享同一范围。未选店铺表示所有店铺。

空集不等于正常经营零销售；每日经营记录覆盖全部店铺 × SKU × 日期才算完整，缺行或 incomplete 标记会产生具体缺失项。金额可聚合用于展示，但证据不完整时 Agent 必须停止并标记 insufficient_data，不创建可执行建议。订单外键、金额非负和唯一约束防止不合法源数据。

## 数据模型

- stores、products、orders、daily_operations：业务事实；daily_operations 为每日覆盖依据。
- analyses：请求范围、状态、完整的 JSON 工作流快照、结果、步骤、错误、请求 ID；idempotency_key 全局唯一且绑定请求内容与创建者。`result` 包含确定性 `metrics`、`findings`、`llm` 运行元数据和 `engine`。
- recommendations：每次分析至多一条建议；证据、影响区间、假设、风险、模拟动作参数、审批信息。`body.llm` 保存本次 LLM 运行结果（成功时含结构化输出）。
- executions：recommendation_id 唯一，idempotency_key 唯一；模拟结果和错误诊断。
- audit_logs：操作人、动作、对象、时间、请求 ID、结果与详情。
- schema_versions、seed_versions：可重复初始化版本记录。

## 身份与状态机

本地演示 Bearer token：demo-analyst → analyst；demo-approver → approver。客户端传 X-Role 或 actor 无效；服务端映射身份。只在 demo mode 提供演示账号元信息。此机制不是生产认证。

建议：proposed → pending_approval → approved → executing → executed；pending_approval → rejected；executing → execution_failed。审批结果不可反转。只有 approver 可以审批、拒绝和模拟执行。analyst 可发起分析、读指标和审计。

条件 UPDATE 抢占审批或执行；执行在单个数据库事务中将 approved 变为 executing、插入唯一执行记录、模拟修改并存结果，最终 executed / execution_failed。等待竞争事务后重复调用返回同一 execution 且 replayed=true，不产生第二条模拟变更。不同建议不能复用执行 Idempotency-Key。拒绝的请求和失败同样审计。

LangGraph 分析图（异常路径）：receive_request → get_sales_metrics → calculate_profit → analyze_profit_drivers → verify_evidence → llm_root_cause_analysis → verify_llm_evidence → create_recommendation → wait_for_approval → END。无异常或数据不完整时从 verify_evidence 直接进入 wait_for_approval，不调用 LLM。每个节点将状态快照提交到数据库；等待为持久化业务暂停点。审批后从数据库加载同一快照进入 LangGraph 执行图：get_approval_status → simulate_execute_approved_action → save_execution_result。不使用仅内存 checkpoint 冒充可恢复性。读快照不重跑已经完成的建议生成。节点超时预算在每次步骤前后检查，失败记录 failed/timeout；不强行终止 Python 阻塞线程。LLM 节点使用 Provider 自身的 `LLM_TIMEOUT` 与有界重试，不叠加步骤超时。启动时将中断的 running 分析标记为 failed（restart_interrupted），保留步骤用于诊断。

## LLM 分析与降级

配置来自后端进程环境（项目根 `.env` 或真实环境变量）：`LLM_PROVIDER`、`LLM_MODEL`（默认 `deepseek-flash`）、`LLM_API_KEY`、`LLM_BASE_URL`（默认 `https://api.deepseek.com`）、`LLM_TIMEOUT`（默认 20s）、`LLM_MAX_TOKENS`（默认 4000）、`LLM_MAX_RETRIES`（默认 1）、`LLM_FALLBACK_POLICY`（`rules` 默认 / `fail`）、`LLM_NATIVE_TOOLS`（默认 true）、`LLM_TOOL_MAX_ROUNDS`（2–8，默认 4）、`LLM_TOOL_MAX_CALLS`（1–12，默认 6）、`LLM_TOOL_MAX_REPEATS`（1–5，默认 2）、`LLM_TOOL_BUDGET_SECONDS`（默认 90）、`LLM_TOOL_RESULT_MAX_BYTES`（512–65536，默认 16384）。`LLM_API_KEY` 使用 SecretStr，API 不返回密钥。未配置 Key 时 `llm_configured=false`，分析仍完成。

LLM 在确定性利润计算、异常归因和证据校验之后运行，输入为已计算的周期、利润桥、主要驱动、8 项证据、SKU 摘要和已规划模拟动作；金额字段为整数美分，输入显式携带 `amount_unit`。启用 `LLM_NATIVE_TOOLS` 时使用原生 Function Calling 的有界循环：请求携带 `tools`，普通轮 `tool_choice=auto`，最后一轮强制 `none`；模型返回的 `tool_calls` 经 JSON、显式允许列表、Registry 存在性、`policy=safe`、`effect=read|pure`、服务端角色、Pydantic 输入 Schema 和本次分析范围（period/store_id 必须完全一致）逐项校验后，才调用服务端只读工具，结果以 `role=tool`、`tool_call_id` 回传，再进入下一轮模型调用。允许模型调用的工具只有 `get_profit_analysis` 与 `get_sku_breakdown`；模型不能调用 `create_recommendation`、审批、执行或其他写工具。任何被拒绝或执行失败的工具调用都会终止循环，按降级策略处理，不会继续尝试写操作。

循环受 `LLM_TOOL_MAX_ROUNDS`、`LLM_TOOL_MAX_CALLS`、相同工具+参数重复次数、总预算 `LLM_TOOL_BUDGET_SECONDS` 和单次结果 `LLM_TOOL_RESULT_MAX_BYTES` 限制；终止原因包括 `final_answer`、`llm_tool_round_limit_exceeded`、`llm_tool_call_limit_exceeded`、`llm_tool_duplicate_call_id`、`llm_tool_repeated_call`、`llm_tool_budget_exceeded`、`llm_tool_scope_mismatch`、`llm_tool_forbidden`、`llm_tool_unknown`、`llm_tool_invalid_arguments`、`llm_tool_execution_failed`、`llm_tool_output_truncated` 和 Provider 错误码。`result.llm` 记录 `native_tools`、`termination_reason`、逐轮 `rounds[{round,finish_reason,tool_call_count,latency_ms,usage}]` 和逐次 `tool_calls[{round,tool_call_id,name,arguments,ok,result_summary,error_code,latency_ms}]`；失败时保留已完成部分。工作台“Agent 执行轨迹”只消费这些字段，不重新实现工具循环：显示轮次、模型、`finish_reason`、工具名、参数摘要、`ok`/失败、错误码、单轮 token 与耗时、总 token/总耗时和终止原因；`ok=false` 必须显示为失败，参数与结果只显示有界摘要，不显示 API Key、认证头或原始请求头。工具参数摘要只展示经过白名单校验的 `period`（ISO 日期）和 `store_id`（受限字符集）；其他字段只显示数量，非法值显示为“已隐藏”，不得对原始参数对象做 JSON 序列化回显。不支持 `chat()` 的 Provider（测试与评测 FakeProvider）继续走单次 `generate_json` 路径，`native_tools=false`。

最终输出必须符合 `LLMAnalysisOutput`：`summary`、`root_causes[{cause,reasoning,evidence_ids}]`、`evidence_references`、`recommendations[{action,rationale,evidence_ids,priority}]`、`expected_impact{direction,horizon_days,explanation}`、`risks`、`missing_information`、`confidence`（0–1）。所有证据 ID 必须是本次 `findings.evidence` 的子集；`verify_llm_evidence` 会遍历全部引用，发现未知 ID 即判定无效，不进入审批流。`finish_reason=length` 导致截断时错误码为 `llm_tool_output_truncated`（原生路径）或 `llm_invalid_json`（单次路径），消息与详情标明截断。LLM 不返回也不覆盖任何利润、成本、销售额数字；动作参数、预期影响金额仍由确定性 `plan_action` 计算。

`result.llm.status` 取值：

- `llm_success`：真实调用成功且 Schema/证据校验通过；`output` 非空，`model`、`latency_ms`、`attempts`、`usage` 来自实际响应；原生路径 `native_tools=true` 且 `termination_reason=final_answer`，并带 `rounds`/`tool_calls` 轨迹。
- `deterministic_fallback`：未配置 Key（`reason=not_configured`）或调用失败/证据引用无效（带 `error`）；本次建议使用确定性文案，`output=null`。
- `llm_failed`：仅当 `LLM_FALLBACK_POLICY=fail`；分析状态 `failed`，错误码 `llm_failed`，运行元数据仍写入 `result.llm`。
- `not_requested`：无异常（`reason=no_anomaly`）或数据不完整（`reason=insufficient_data`），LLM 未被触发。

`engine` 仅在 `llm_success` 时为 `deepseek-llm-v1`，否则为 `deterministic-rules-v1`。审计动作：`analysis.llm_succeeded`、`analysis.llm_fallback`（outcome=degraded）、`analysis.llm_failed`。Provider 错误码包括 `llm_auth_failed`、`llm_insufficient_balance`、`llm_rate_limited`、`llm_server_error`、`llm_http_error`、`llm_timeout`、`llm_network_error`、`llm_invalid_json`、`llm_empty_response`、`llm_schema_invalid`、`llm_invalid_tool_call`、`invalid_evidence_reference`；原生工具循环另有上文列出的 `llm_tool_*` 错误码。

## API

全部 JSON；`/health` 无认证，其余 API 除 `/api/demo` 外需 Bearer。金额字段为整数美分，currency=USD。错误：`{error:{code,message,details},request_id}`；响应含 X-Request-ID。

- GET /health：数据库连通性、simulation 标志，以及 `llm.{configured,provider,model}`（不含密钥）。
- GET /api/demo：demo mode、演示身份、可用店铺和数据日期（可空），以及 `llm.{configured,provider,model,fallback_policy}`。
- POST /api/demo/seed：analyst 可重复初始化，不清除历史。
- GET /api/dashboard/summary：period、previous_period、current、previous、changes、trend、drivers、sku_breakdown、data_quality、anomaly。
- GET /api/metrics：同一指标契约（支持范围/店铺）。
- POST /api/analyses：`{start_date?,end_date?,store_id?}`，可选 Idempotency-Key；202 返回 queued 分析，FastAPI 后台启动有状态图。
- GET /api/analyses、GET /api/analyses/{id}：执行步骤、工具摘要、证据、错误、`result.engine` 和 `result.llm`；原生路径的 `result.llm` 另含 `native_tools`、`termination_reason`、`rounds` 和 `tool_calls`。
- GET /api/tools：工具输入输出 JSON Schema、权限和副作用分类。
- GET /api/recommendations、GET /api/recommendations/{id}：建议及 simulation 动作与执行记录。
- POST /api/recommendations/{id}/approve 或 /reject：`{reason}`，仅 pending_approval；409 表示状态冲突。
- POST /api/recommendations/{id}/execute：必须 Idempotency-Key；批准后执行，仅模拟。
- GET /api/audit-logs：最新在前，limit 1..200；支持 object_id 与 request_id 精确过滤（request_id 最长 64 字符）。

时间戳字段统一为 UTC ISO 8601 并带 `Z` 后缀；SQLite 与 PostgreSQL 输出一致，前端按 UTC 解析后本地化显示。

工具统一返回 `{ok,data,error}`，Pydantic 校验输入与输出；权限在 Tool Registry 与业务处理器双层验证。当前注册 8 个工具；`get_profit_analysis` 与 `get_sku_breakdown` 是面向模型的原生只读工具，输入只有 `MetricsInput`（period/store_id），由服务端用可信账本调用既有 `fetch_metrics`/`build_report`/`analyze_drivers` 计算，模型不能提交 `RawMetrics` 或完整报告。每个工具在 `/api/tools` 声明 `policy`：`safe`（只读、纯计算或仅写本系统提案）、`ask`（必须经人工审批后执行的模拟动作）、`deny`（外部系统副作用，注册表直接拒绝，当前未注册）。`ask` 工具由审批状态机与 approver 角色双重约束。模型可见工具另受 `LLM_TOOL_ALLOWLIST`、`effect`、`policy` 和角色四重限制；`create_recommendation` 虽为 `safe`，但 `effect=database_write`，不会暴露给模型。工具参数失败为 invalid_tool_input，执行失败保留代码和消息。

## 验收

pytest 覆盖利润边界、环比、归因加总、缺失数据、LangGraph 路径与失败、Schema、身份伪造、审批冲突、并发幂等、审计过滤、工具策略、UTC 时间戳与 Seed 保留历史。LLM 测试覆盖 Provider 成功/重试/鉴权/超时/非法 JSON/空响应、环境变量读取、结构化 Schema、证据引用校验、无 Key 降级、调用失败降级、Schema 无效降级、`fail` 策略以及 LLM 成功后的审批与幂等执行；原生 Function Calling 测试覆盖请求携带 `tools`/`tool_choice`、`tool_calls` 解析、多轮工具调用、禁止写工具/未知工具/非法参数/越界范围、轮次/次数/重复/预算上限、Provider 超时与工具异常降级、结果截断和工作流轨迹持久化。全部使用 MockTransport/FakeProvider，不消耗真实 Token；原生 Function Calling 另执行了 3 次真实 `deepseek-flash` 脚本运行，第 3 次完整成功（2 个只读工具调用、`final_answer`、`all_references_valid=true`），第 1 次网络错误降级，见 `docs/LLM_LIVE_CHECK.md`。独立 JSON 样本验证业务结论。Vitest + RTL 覆盖启动、轮询、审批、动作预览、执行、审计过滤、API 错误、LLM 成功/降级面板，以及原生轨迹的正常轮次、工具参数摘要、逐轮 token、失败调用错误码和单次 JSON 路径说明；生产构建和实际 HTTP + 浏览器演示闭环。Phase 2D 原生评测测试覆盖 fixture 到临时账本的可复现性、正常多轮、多余工具、未知/写/审批工具、非法参数、越界范围、重复调用、轮次上限、工具执行失败、非法最终 JSON、非法证据、数据不完整跳过、真实入口授权/预算/未配置检查，以及 CLI 失败退出码；全部使用 `FakeToolProvider`，不消耗真实 Token。PostgreSQL 已在本机 Docker Desktop 完成容器验收；其他环境若无法运行 Docker，需明确记录验证缺口。真实 DeepSeek 单次 JSON 调用已通过 `scripts/llm_check.py` 完成一次：`deepseek-flash`、15467.974 ms、4801 tokens、`all_references_valid=true`，详见 `docs/LLM_LIVE_CHECK.md`。

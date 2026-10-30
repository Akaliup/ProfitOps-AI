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
- analyses：请求范围、状态、完整的 JSON 工作流快照、结果、步骤、错误、请求 ID；idempotency_key 全局唯一且绑定请求内容与创建者。
- recommendations：每次分析至多一条建议；证据、影响区间、假设、风险、模拟动作参数、审批信息。
- executions：recommendation_id 唯一，idempotency_key 唯一；模拟结果和错误诊断。
- audit_logs：操作人、动作、对象、时间、请求 ID、结果与详情。
- schema_versions、seed_versions：可重复初始化版本记录。

## 身份与状态机

本地演示 Bearer token：demo-analyst → analyst；demo-approver → approver。客户端传 X-Role 或 actor 无效；服务端映射身份。只在 demo mode 提供演示账号元信息。此机制不是生产认证。

建议：proposed → pending_approval → approved → executing → executed；pending_approval → rejected；executing → execution_failed。审批结果不可反转。只有 approver 可以审批、拒绝和模拟执行。analyst 可发起分析、读指标和审计。

条件 UPDATE 抢占审批或执行；执行在单个数据库事务中将 approved 变为 executing、插入唯一执行记录、模拟修改并存结果，最终 executed / execution_failed。等待竞争事务后重复调用返回同一 execution 且 replayed=true，不产生第二条模拟变更。不同建议不能复用执行 Idempotency-Key。拒绝的请求和失败同样审计。

LangGraph 分析图：receive_request → get_sales_metrics → calculate_profit → analyze_profit_drivers → verify_evidence → create_recommendation → wait_for_approval → END。每个节点将状态快照提交到数据库；等待为持久化业务暂停点。审批后从数据库加载同一快照进入 LangGraph 执行图：get_approval_status → simulate_execute_approved_action → save_execution_result。不使用仅内存 checkpoint 冒充可恢复性。读快照不重跑已经完成的建议生成。节点超时预算在每次步骤前后检查，失败记录 failed/timeout；不强行终止 Python 阻塞线程。启动时将中断的 running 分析标记为 failed（restart_interrupted），保留步骤用于诊断。

## API

全部 JSON；`/health` 无认证，其余 API 除 `/api/demo` 外需 Bearer。金额字段为整数美分，currency=USD。错误：`{error:{code,message,details},request_id}`；响应含 X-Request-ID。

- GET /health：数据库连通性与 simulation 标志。
- GET /api/demo：demo mode、演示身份、可用店铺和数据日期（可空）。
- POST /api/demo/seed：analyst 可重复初始化，不清除历史。
- GET /api/dashboard/summary：period、previous_period、current、previous、changes、trend、drivers、sku_breakdown、data_quality、anomaly。
- GET /api/metrics：同一指标契约（支持范围/店铺）。
- POST /api/analyses：`{start_date?,end_date?,store_id?}`，可选 Idempotency-Key；202 返回 queued 分析，FastAPI 后台启动有状态图。
- GET /api/analyses、GET /api/analyses/{id}：执行步骤、工具摘要、证据和错误。
- GET /api/tools：工具输入输出 JSON Schema、权限和副作用分类。
- GET /api/recommendations、GET /api/recommendations/{id}：建议及 simulation 动作与执行记录。
- POST /api/recommendations/{id}/approve 或 /reject：`{reason}`，仅 pending_approval；409 表示状态冲突。
- POST /api/recommendations/{id}/execute：必须 Idempotency-Key；批准后执行，仅模拟。
- GET /api/audit-logs：最新在前，limit 1..200；支持 object_id 与 request_id 精确过滤（request_id 最长 64 字符）。

时间戳字段统一为 UTC ISO 8601 并带 `Z` 后缀；SQLite 与 PostgreSQL 输出一致，前端按 UTC 解析后本地化显示。

工具统一返回 `{ok,data,error}`，Pydantic 校验输入与输出；权限在 Tool Registry 与业务处理器双层验证。每个工具在 `/api/tools` 声明 `policy`：`safe`（只读、纯计算或仅写本系统提案）、`ask`（必须经人工审批后执行的模拟动作）、`deny`（外部系统副作用，注册表直接拒绝，当前未注册）。`ask` 工具由审批状态机与 approver 角色双重约束。工具参数失败为 invalid_tool_input，执行失败保留代码和消息。

## 验收

pytest 覆盖利润边界、环比、归因加总、缺失数据、LangGraph 路径与失败、Schema、身份伪造、审批冲突、并发幂等、审计过滤、工具策略、UTC 时间戳与 Seed 保留历史。独立 JSON 样本验证业务结论。Vitest + RTL 覆盖启动、轮询、审批、动作预览、执行、审计过滤和 API 错误；生产构建和实际 HTTP + 浏览器演示闭环。PostgreSQL 若本机 Docker 引擎不可用需明确记录验证缺口。

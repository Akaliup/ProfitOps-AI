# 架构与关键决策

## 运行边界

```
React 工作台 (5180) --真实 HTTP--> FastAPI (8010) --SQLAlchemy--> SQLite / PostgreSQL
                                          |
                                          +-- LangGraph 分析图（7 个持久化步骤）
                                          +-- LangGraph 执行图（3 个步骤，事务内）
                                          +-- Tool Registry（6 个 Pydantic 工具）
                                          +-- 审计事件 + 请求 ID
```

默认不依赖 LLM API Key，也不调用真实广告、采购或支付系统。分析结论由确定性规则和数据库账本产生；`engine=deterministic-rules-v1` 在分析、建议和健康接口中可见。这里不是“模型假装成规则”，也没有把 mock 结果冒充真实 LLM。

## 为什么选择 SQLite 默认、PostgreSQL 可选

面试演示需要一条无需外部服务即可复现的路径，因此本地默认 SQLite（WAL、外键、30s busy timeout）。生产式容器路径使用 PostgreSQL 16 + psycopg，Compose 提供健康检查和持久卷。数据库事实模型、唯一约束和条件 UPDATE 在两者通用。由于本机 Docker 引擎未运行，本次只在 SQLite 上完成真实运行验证；Compose 语法已验证，容器运行不作为已通过项。

## 利润口径与归因

金额在数据库和 API 中均为整数美分，避免浮点误差。订单成本字段彼此独立；广告支出来自 `daily_operations`，不在订单中重复。退款仅扣减收入，采购成本已经是退货后残值，避免重复扣减。利润率与环比使用 Decimal 四舍五入到两位；分母为零返回 null，不伪造 0%。利润桥的每项贡献为：

- 销售额：`current - previous`
- 折扣、退款、采购、广告、履约、平台费、其他：`-(current - previous)`

八项之和必须等于净利润变化；否则证据校验失败。主要驱动按负向会计贡献排序，文案明确为“利润桥归因”，不声称因果。

## 工作流与持久化

`run_analysis` 在后台执行 LangGraph 分析图。每个节点开始和完成时更新 `analyses.steps` 与 `snapshot`；`wait_for_approval` 是真实的业务暂停点，不是内存等待。执行阶段从数据库快照进入单独执行图，读取审批状态、调用唯一模拟执行工具并保存结果，避免重跑建议生成。失败会写入 `failed` 状态、失败步骤和错误码；重启恢复将遗留 `queued/running` 分析标记为 `restart_interrupted`。超时在步骤完成后检查实际耗时（`step_timeout`），不声称能够强杀同步线程。

## 状态机与并发

`proposed → pending_approval → approved → executing → executed`，另有 `rejected` 和 `execution_failed` 终态。审批和执行都通过带旧状态条件的 UPDATE 抢占状态；执行先抢占行锁/写锁，再检查唯一执行记录，最后在同一个事务中插入执行记录、模拟变更、更新状态和写审计。`executions.recommendation_id` 与 `executions.idempotency_key` 唯一。重复请求返回已有执行记录并标记 `replayed=true`；不同建议复用执行键返回 409。失败保留错误诊断并返回 HTTP 500，不会伪装成成功。

## 权限与安全边界

演示 Bearer token 在服务端映射为 analyst / approver。客户端传入的 `X-Role`、`X-Actor` 不参与身份判断。Tool Registry 和业务服务都做角色校验，普通分析师即使调用 API 或隐藏前端按钮也无法批准或执行。

工具在 `/api/tools` 中显式声明 `policy`：`safe` 表示只读、纯计算或仅写入本系统提案；`ask` 表示需要人工审批后才能执行的模拟动作；`deny` 表示外部系统副作用，注册表在调用前直接拒绝（当前没有注册任何 deny 工具）。`simulate_execute_approved_action` 是唯一的 `ask` 工具，同时受 approver 角色、`approved` 条件 UPDATE 和唯一执行记录约束。审批页面在执行前展示待模拟动作的参数（SKU、参考日预算、模拟新预算），避免“只看到建议、看不到具体动作”。

审计接口支持按 `object_id` 或 `request_id` 精确过滤；时间戳统一输出 UTC ISO 8601（`Z`），SQLite 与 PostgreSQL 行为一致。这个机制明确是本地演示身份，不是生产认证；生产需要 OIDC、租户隔离、密钥管理、速率限制和真实授权策略。

## 前端

React + TypeScript + Vite + 自定义 CSS，Recharts 只负责趋势图。所有页面数据通过真实 API 获取，无静态经营假数据。关键状态包括：初始加载、空数据初始化、API 错误（带请求 ID）、分析轮询、建议审批、待执行动作预览、重复执行回放和手机布局。审计页支持按请求 ID 精确过滤；趋势图提供表格替代视图；颜色使用通过 CVD 校验的蓝/橙两色，并始终有图例与数据表。

## 数据初始化与迁移

首次启动自动建表并加载固定种子 `demo-60d-v1`：2026-09-01 至 2026-10-30、2 店铺、3 SKU，订单和每日广告/库存记录均由确定性规则生成。`seed_versions` 与事务保证重复调用不重复插入，也不清除审批、审计历史。MVP 采用版本化初始化和数据库快照，不包含 Alembic 增量迁移；生产化前需要补齐迁移和备份策略。

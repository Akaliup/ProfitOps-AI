# ProfitOps AI 路线图

更新：2026-11-01  
维护：Claude Code；每个阶段开始与结束时同步更新本文件与 `docs/STATUS.md`。

## 编制依据

此前仓库没有独立的 `ROADMAP.md`。本文件依据 `TASKS.md` 的「后续（核心通过后再评估）」、`docs/HANDOFF.md` 第 8 节和 `docs/INTERVIEW.md` 的未实现项，把后续工作拆成可独立验收的阶段。阶段顺序可按用户批准调整；未获批准不自行进入下一阶段。

## 阶段总览

| 阶段 | 目标 | 优先级 | 状态 | 依赖 | 分支 / PR |
|---|---|---|---|---|---|
| 2C | MCP Server（stdio，只读工具） | P0（Agent 能力） | 进行中 | 最新 `master` | `feat/phase-2c-mcp-server` |
| 2D | Schema 演进与持久化任务队列 | P1（生产化前必须） | 计划 | 2C 合并 | 待定 |
| 3 | 生产身份与多租户 | P2（对外部署前） | 计划 | 2D 合并 | 待定 |

## Phase 2C：MCP Server（stdio，只读工具）

### 目标

把现有 Tool Registry 中的只读/纯计算工具通过 MCP（Model Context Protocol）暴露给本地 MCP 客户端，验证工具协议、Schema 与策略边界，同时保持审批和执行仍只走 HTTP + 人工审批。

### 范围

- 使用官方 Python MCP SDK，stdio transport；不监听网络端口。
- 暴露 4 个 `safe` 工具：`get_sales_metrics`、`calculate_profit`、`analyze_profit_drivers`、`get_approval_status`。
- 不暴露 `create_recommendation`（数据库写入）和 `simulate_execute_approved_action`（`ask`，审批后动作）。
- 复用 Tool Registry 的 Pydantic Schema、`policy` 和服务端角色校验；角色由服务端环境变量决定，默认 `analyst`，不接受客户端传入角色。
- 工具错误映射为 MCP tool error，不使服务崩溃；调用不写数据库、不调用 LLM、不产生外部副作用。

### 非目标

网络 transport、OAuth、多租户、审批/拒绝/执行工具、真实 LLM 调用、MCP prompts/resources。

### 验收条件

- MCP 客户端可完成 `initialize`、`tools/list`、`tools/call`；4 个工具均能成功返回。
- 未注册工具、非法参数、`ask` 工具未暴露、角色越权均有自动化测试。
- 调用前后数据库业务表计数不变；无 LLM 调用；无外部副作用。
- `python -m app.mcp_server` 可启动；`docs/MCP.md` 提供客户端配置示例与安全边界说明。
- 后端 pytest、前端测试、生产构建与 GitHub CI 通过。

### 风险

- MCP SDK 与 FastAPI/Starlette 的依赖兼容性；通过固定版本、锁文件和完整后端测试控制。
- stdio 进程的数据库路径与环境变量需与主服务一致；文档明确要求先初始化数据库。

## Phase 2D：Schema 演进与持久化任务队列

### 目标

让数据库升级和长任务不再依赖 `create_all` + 进程内 `BackgroundTasks`，为多 worker 和生产化打基础。

### 范围

- 引入 Alembic：初始迁移覆盖当前模型；后续迁移可增量升级。
- 分析任务改为持久化队列（数据库轮询 worker 或等价方案），保留现有状态机、步骤快照和 `restart_interrupted` 语义。
- 任务抢占使用条件 UPDATE；同一任务不被多个 worker 重复执行。
- 提供迁移与 worker 的运行说明。

### 依赖

- Phase 2C 已合并。
- 现有 API、数据库和状态机契约不变；如需变更，同步 `backend/tests`、`frontend/src/types.ts` 和 `docs/CONTRACT.md`。

### 优先级

P1。生产化前必须完成。

### 验收条件

- 空库 `alembic upgrade head` 可建出完整 Schema；已有库升级不丢失审批、执行和审计数据。
- worker 重启后 `queued/running` 任务可恢复或被明确标记失败；并发抢占只产生一次执行。
- 迁移、队列和恢复路径有自动化测试；`docs/TEST_REPORT.md` 记录实际命令与结果。
- CI 通过，且不依赖真实 LLM。

### 风险

- 迁移与现有 `schema_versions` / `seed_versions` 的兼容。
- 多 worker 语义、任务租约和重复执行边界需要明确测试。

## Phase 3：生产身份与多租户

### 目标

用 OIDC/租户上下文替代公开演示 token，隔离数据、审批和审计。

### 范围

- 认证适配层：从 token 解析 tenant 与 role，服务端决定角色。
- `tenant_id` 进入核心表与查询；分析、建议、执行、审计按租户过滤。
- 审批与执行增加租户条件；跨租户访问返回 403/404。
- 限流、请求体大小限制、CORS 与安全响应头。
- 契约、测试与前端类型同步。

### 依赖

- Phase 2D 已合并（需要 Alembic 迁移能力）。
- 生产身份提供方的配置与密钥管理方案。

### 优先级

P2。对外部署前完成；范围较大，可拆为 3A（身份与租户隔离）和 3B（限流与安全加固）。

### 验收条件

- 无有效 token 返回 401；跨租户读写被拒绝；审批/执行带租户条件。
- 审计可按租户与请求 ID 追踪；限流与安全响应头有测试。
- 不泄露密钥；真实身份提供方测试需单独授权并记录调用。
- CI 通过；契约与前端类型同步。

### 风险

- 租户模型会影响现有单租户演示数据，需要迁移与兼容策略。
- OIDC 接入涉及外部环境，需单独授权，不能进入普通 CI。

## 阶段准入与退出

- 每个阶段开始前由用户批准范围；开始后 Claude 自主完成实现、测试、修复、提交、推送和 PR。
- 阶段完成后停止新增功能，等待用户确认合并或批准下一阶段。
- PR 一律人工合并；CI 通过或 Codex 未发现问题都不构成自动合并理由。
- 阶段状态、分支、CI、Codex Review 与阻塞记录在 `docs/STATUS.md`。

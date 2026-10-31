# ProfitOps MCP Server（只读，stdio）

Phase 2C 提供本地 MCP（Model Context Protocol）Server，把现有 Tool Registry 中的只读/纯计算工具暴露给 MCP 客户端。审批、拒绝和模拟执行仍只能走 HTTP + 人工审批，不通过 MCP 暴露。

## 暴露的工具

| 工具 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `get_sales_metrics` | `MetricsInput`（period、store_id） | `RawMetrics` | 原始订单/每日经营聚合与数据覆盖；`net_sales`、`net_profit` 等派生字段由下一步计算 |
| `calculate_profit` | `CalculateInput`（metrics） | `MetricsReport` | 整数美分利润、环比、贡献桥与异常标记 |
| `analyze_profit_drivers` | `DriversInput`（report） | `DriverReport` | 会计贡献项、主要驱动与证据 |
| `get_approval_status` | `ApprovalInput`（recommendation_id） | `ApprovalOutput` | 只读查询服务端审批状态 |

所有工具都来自 `backend/app/tools.py` 的 Tool Registry，复用同一套 Pydantic Schema、角色校验和错误格式。MCP 层不另写业务逻辑。

## 明确不暴露

- `create_recommendation`：会写数据库，属于提案写入；Phase 2C 的 MCP 面保持只读。
- `simulate_execute_approved_action`：`ask` 工具，必须经过 HTTP 审批流和 approver 角色；MCP 客户端调用会返回 `unknown_tool`。

## 安全边界

- **角色由服务端决定**：`PROFITOPS_MCP_ROLE` 只能是 `analyst`（默认）或 `approver`；工具参数里传入 `role` 会被 Schema 拒绝（`invalid_tool_input`），不接受客户端角色头。
- **只读**：暴露的 4 个工具不写业务表；测试会对比调用前后 `analyses`、`recommendations`、`executions`、`audit_logs` 计数。
- **不调用 LLM**：MCP 进程不构建 LLM Provider；文档与验证脚本都使用 `LLM_PROVIDER=disabled`、`LLM_API_KEY=`。
- **无外部副作用**：不连接广告、采购、支付或任何外部经营系统。
- **数据库需先初始化**：MCP Server 不建表、不 Seed；请先用 `python scripts/dev.py` 或主服务初始化数据库，或指向一个已初始化的数据库副本。

## 启动

```bash
# 依赖已在 .venv 中安装
PYTHONPATH=backend .venv/Scripts/python.exe -m app.mcp_server
```

Windows PowerShell 可写：

```powershell
$env:PYTHONPATH="backend"; .venv/Scripts/python.exe -m app.mcp_server
```

Server 使用 stdio transport，JSON-RPC 走 stdout；不会监听网络端口。

## MCP 客户端配置示例

把路径替换为本机绝对路径：

```json
{
  "mcpServers": {
    "profitops": {
      "command": "E:/ProfitOps-AI/.venv/Scripts/python.exe",
      "args": ["-m", "app.mcp_server"],
      "env": {
        "PYTHONPATH": "E:/ProfitOps-AI/backend",
        "PROFITOPS_DATABASE_URL": "sqlite:///E:/ProfitOps-AI/data/profitops.db",
        "PROFITOPS_MCP_ROLE": "analyst",
        "LLM_PROVIDER": "disabled",
        "LLM_API_KEY": ""
      }
    }
  }
}
```

POSIX 环境把 `command` 换成 `.venv/bin/python`，路径使用 `/`。如果只想做只读实验，建议把数据库复制到临时路径，不要指向正在写入的运行库。

## 调用链示例

1. `get_sales_metrics`：传 `{"period": {"start_date": "2026-10-01", "end_date": "2026-10-30"}}`。
2. 把返回的 `data` 作为 `metrics` 传给 `calculate_profit`，得到 `net_sales`、`net_profit`、`bridge_verified`。
3. 把 `calculate_profit` 返回的 `data` 作为 `report` 传给 `analyze_profit_drivers`，得到主要驱动和证据。
4. `get_approval_status` 传 `recommendation_id`，只读查询审批状态。

## 验证

```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_mcp_server.py -q
.venv/Scripts/python.exe scripts/mcp_check.py
```

`scripts/mcp_check.py` 使用临时数据库和真实 stdio 子进程，验证：

- `initialize`、`tools/list`、`tools/call` 可用；
- 只列出 4 个只读工具；
- `calculate_profit` 返回非零整数美分与 `bridge_verified=true`；
- `simulate_execute_approved_action` 不在列表中，直接调用返回 `unknown_tool`；
- 子进程使用 `LLM_PROVIDER=disabled`、`LLM_API_KEY=`，不调用真实模型。

## 限制

- 仅 stdio；没有网络 transport、OAuth、多租户和限流。
- 不暴露审批/执行工具；这些动作继续走 HTTP 状态机。
- `get_sales_metrics` 返回原始聚合，`net_sales`/`net_profit` 需要 `calculate_profit` 计算；这是为了与现有 LangGraph 工作流保持一致。

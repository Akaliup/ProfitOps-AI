# ProfitOps AI 开发约定

- 范围仅限本项目。默认规则引擎 + LangGraph，不调用付费模型或外部经营系统。
- 所有执行必须为 simulation，不对真实广告、采购、支付产生影响。
- Python 3.11+ / FastAPI / SQLAlchemy / LangGraph；React / TypeScript / Vite。
- 金额以 USD 整数美分储存和通过 API 传输，前端除以 100 格式化。日期为 UTC 业务日，区间两端包含。
- 利润口径及 API、数据库、状态机契约见 docs/CONTRACT.md；变更契约必须同步测试与前端类型。
- 服务端决定角色。演示 Bearer token 固定映射为 analyst / approver，不信任角色请求头。
- 审批和执行依赖条件 UPDATE、事务和唯一约束；模拟执行记录是唯一副作用。
- 用独立数据库测试；不能清空用户运行库。Seed 可重复执行且不删除审批、审计数据。
- 相关验证：`python -m pytest backend/tests -q`、`npm --prefix frontend test`、`npm --prefix frontend run build`、`python scripts/mcp_check.py`、`python scripts/smoke.py`。
- 不推送远端，不部署公网；提交须由用户明确要求。

## PR 与审查规范

- 功能分支向 `master` 提 PR；不直接向 `master` 推送，不使用 `force push`、`reset --hard` 或清理未提交修改。
- PR 描述必须列出实际执行的验证命令和结果；未执行的检查明确标注“未验证”，不得用 CI 通过代替真实 LLM、容器或浏览器验证。
- 自动 CI 固定使用空 `LLM_API_KEY` 和临时测试数据库；真实 LLM 调用不得进入自动 CI，必须单独授权并记录调用次数与报告。
- 审查必须覆盖：整数美分与利润守恒、证据 ID 可追溯、LLM 失败/降级状态可见、审批与执行幂等、服务端角色校验、无真实外部副作用。
- 禁止提交 `.env`、API Key、数据库、缓存、构建产物和本机运行数据；发现泄露立即停止推送并轮换密钥。
- 契约变更必须同步 `backend/tests`、`frontend/src/types.ts` 和 `docs/CONTRACT.md`；涉及评测的变更同步 `docs/EVALUATION.md`。
- 自动审查工具（如 Codex/Copilot）的评论只是辅助；未实际出现审查结果时，不得声称已审查。
- PR 不自动合并；合并前至少通过 CI 并由人工审查确认。

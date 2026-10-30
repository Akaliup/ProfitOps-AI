# ProfitOps AI 开发约定

- 范围仅限本项目。默认规则引擎 + LangGraph，不调用付费模型或外部经营系统。
- 所有执行必须为 simulation，不对真实广告、采购、支付产生影响。
- Python 3.11+ / FastAPI / SQLAlchemy / LangGraph；React / TypeScript / Vite。
- 金额以 USD 整数美分储存和通过 API 传输，前端除以 100 格式化。日期为 UTC 业务日，区间两端包含。
- 利润口径及 API、数据库、状态机契约见 docs/CONTRACT.md；变更契约必须同步测试与前端类型。
- 服务端决定角色。演示 Bearer token 固定映射为 analyst / approver，不信任角色请求头。
- 审批和执行依赖条件 UPDATE、事务和唯一约束；模拟执行记录是唯一副作用。
- 用独立数据库测试；不能清空用户运行库。Seed 可重复执行且不删除审批、审计数据。
- 相关验证：`python -m pytest backend/tests -q`、`npm --prefix frontend test`、`npm --prefix frontend run build`、`python scripts/smoke.py`。
- 不推送远端，不部署公网；提交须由用户明确要求。

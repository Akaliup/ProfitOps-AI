# 九页工作台交付说明

本文记录当前分支实际交付的范围。页面上的金额来自当前租户账本，不是交互原型里的晴川人民币样例，也不是生产结果。

## 启动与隔离库

推荐命令（只使用 `data/demo-copy.db`，不写入、不迁移、不清空 `data/profitops.db`）：

```bash
python scripts/dev.py --demo-db
```

脚本会创建 `.venv`、安装 `backend/requirements.lock.txt` 与前端依赖，然后启动 API 和 Vite。依赖已装好时可以加 `--skip-install`。

`PROFITOPS_DEMO_MODE` 与 `PROFITOPS_AUTO_SEED` 的默认值都是 `true`。因此只启动 uvicorn、不再单独调用 `POST /api/demo/seed`，空库也会在进程启动时写入：

- 演示身份 `demo-analyst` / `demo-approver`
- 种子版本 `demo-60d-v1`（2026-09-01 至 2026-10-30，两店三 SKU）
- 知识库 `knowledge-v1`

重复启动看到已有种子版本就返回，不删除订单、审批、执行或审计。要重置演示，只删除 `data/demo-copy.db`（以及它的 `-wal`/`-shm`）。不要删除 `data/profitops.db`。

未设置 `LLM_API_KEY` 时 `/health` 与 `/api/demo` 的 `llm.configured` 为 false。分析状态是 `deterministic_fallback` 或 `not_requested`，界面不会把规则结果写成模型成功。

工作台地址是本机 `http://127.0.0.1:5180`。这不是公网地址。

## 各页实际状态

| 页面 | 已实现 | 禁用或未做 |
|---|---|---|
| 经营总览 | 周期、店铺、KPI、趋势、利润桥、分店加总、异常待办。数字来自 `GET /api/dashboard/summary` 与 `GET /api/workspace/overview` | 不展示原型里的虚构人民币 |
| 助手中心 | 经营调查、库存、广告预算、调价校验会打开真实页面或现有分析 | 客服、财务，以及商品运营里的转化/跟价。这些按钮不能发起任务 |
| 经营调查 | 现有 LangGraph 分析、证据、知识检索、执行轨迹、失败重试 | 不并行多个子 Agent。无 Key 时明确降级 |
| 库存建议 | `GET /api/inventory`；补货提案只含 CRITICAL/HIGH/MEDIUM 且数量大于 0 的 SKU | 不连接 ERP。超储数量为 0，不进入提案 |
| 广告分析 | 账本广告费可生成 `adjust_ad_budget` 模拟。已导入且不与每日账单冲突的活动行才计算 ROAS | 种子账本已有每日广告行，`GET /api/workspace/demo-csv/ads` 返回 409，避免花费双计。不连接广告后台 |
| 知识与数据 | 订单/广告/库存 CSV 预览与提交、批次、知识检索与文档维护 | 天猫、京东、抖音、ERP 未连接 |
| 审批中心 | 同一套建议状态机：差异、证据、通过、驳回、幂等模拟执行。调价低于 28% 时服务端 `margin_redline` | 不改价、不改预算、不采购、不付款。`external_calls` 为 0 |
| 运行与评测 | 任务、失败、审计，以及 `POST /api/evals/offline` 当场重算的检查 | 没有手写通过率，没有 Langfuse，没有 LLM-as-judge |
| 设置与权限 | `GET /api/workspace/access` 的分析师/审批负责人矩阵和集成状态 | 广告专员、仓配、只读访客在后端不存在，矩阵全是禁用 |

经营周报仍在「库存建议」里的「经营周报与库存」，沿用原来的周报 API。

## 持久化路径

| 动作 | API | 数据库 |
|---|---|---|
| 总览与分店 | `GET /api/dashboard/summary`、`GET /api/workspace/overview` | 只读 `orders`、`daily_operations` |
| 活动 ROAS | `GET /api/workspace/campaigns` | 只读 `source_records.kind=ads` |
| 库存 | `GET /api/inventory` | 只读 `source_records.kind=inventory` 与订单 |
| 演示 CSV | `GET /api/workspace/demo-csv/{orders,inventory}` | 不写库。`ads` 直接 409 |
| 导入 | `POST /api/data/{kind}/preview` 与 `/commit` | `import_batches`、`source_records`；订单再写 `orders`。相同自然键且内容一致为 noop，内容不同则拒绝，不覆盖 |
| 调查 | `POST /api/analyses` | `analyses` |
| 补货 / 预算 / 调价 | `POST /api/proposals/replenishment`、`/budget`、`/price` | 新建 `analyses` 与 `recommendations`，幂等键含账本指纹 |
| 审批与执行 | `POST /api/recommendations/{id}/approve|reject|execute` | `recommendations`、`executions`、`audit_logs` |
| 评测 | `POST /api/evals/offline`、`GET /api/evals` | `audit_logs.action=eval.completed` |
| 知识库 | 原有 `/api/knowledge/*` | `knowledge_documents`、`knowledge_chunks` |

金额在库内和 API 中是 USD 整数美分。审批状态在 SQLite 行上，进程重启后仍可查询和执行；重复执行返回同一条 `executions` 且 `replayed=true`。

## 权限

角色只看服务端 token。`X-Role` 不能把分析师变成审批人。分析师可以预览和起草，不能提交导入、批准或执行。另一租户访问本租户的店铺、建议或评测记录得到 404，看不到是否存在。匿名请求这些接口得到 401。

## 尚未验收

- PostgreSQL：本环境没有 `PROFITOPS_TEST_POSTGRES_URL`，也没有在本轮启动 Postgres。SQLite 上的迁移测试已跑；Postgres 迁移测试被跳过。
- 真实 DeepSeek、远程 embedding、Langfuse、店铺、广告后台、ERP、支付都没有连接。
- 原型中的晴川三店人民币数没有写入账本。

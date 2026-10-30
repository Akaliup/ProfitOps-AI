# ProfitOps AI

**跨境电商利润经营决策 Agent。** 销售额上涨、利润下降时，ProfitOps 从真实经营账本计算利润变化，定位会计贡献项，核验证据，生成待审批建议，经人工批准后执行**受控模拟**，并留下完整审计记录。

默认规则引擎 + LangGraph，**不需要 LLM API Key**；所有执行都明确标记 `simulation=true`，不调用真实广告、采购或付款系统。

![经营总览](docs/screenshots/overview.png)

<details>
<summary>审批与模拟执行截图</summary>

![建议与证据](docs/screenshots/approval.png)

![模拟执行结果](docs/screenshots/execution.png)
</details>

## 快速启动（本地，推荐）

前置：Python 3.11+、Node.js 20.19+、npm。无需 Docker 或外部数据库。

```powershell
git clone <repo> ProfitOps-AI
cd ProfitOps-AI
python scripts/dev.py
```

脚本会创建 `.venv`、按锁定版本安装依赖、自动初始化 60 天演示数据，并启动：

- 工作台：<http://127.0.0.1:5180>
- API 文档：<http://127.0.0.1:8010/docs>
- 健康检查：<http://127.0.0.1:8010/health>

端口被占用时可指定：`python scripts/dev.py --api-port 8020 --web-port 5190`。依赖已安装时可加 `--skip-install`。数据库位于 `data/profitops.db`；删除它可做全新初始化（会清除审批与审计历史）。

### Docker Compose（可选 PostgreSQL）

```powershell
docker compose up --build
```

工作台 <http://127.0.0.1:8080>，API <http://127.0.0.1:8010>。Compose 使用 PostgreSQL 16 和持久卷；只绑定回环地址。当前交付环境中 Docker 引擎未运行，因此容器路径**未完成实际运行验证**，详见 `docs/TEST_REPORT.md`。

在 Docker 可用的机器上，可用以下命令做一次容器验收：

```bash
docker compose up --build -d
python scripts/smoke.py --base-url http://127.0.0.1:8010
curl -I http://127.0.0.1:8080
docker compose logs --tail=50 backend
docker compose down
```

## 演示流程（约 3 分钟）

1. 打开工作台。默认身份是“经营分析师”；总览显示销售额上涨、净利润下降，KPI、趋势图、利润归因和 SKU 明细均来自 API。
2. 点击 **启动 Profit Agent**。工作台逐步显示读取数据 → 利润计算 → 异常归因 → 证据核验 → 创建建议 → 等待审批，每步有耗时、摘要和请求 ID。
3. 点击 **查看经营建议**。查看统计周期、上一周期、8 项利润贡献、预期影响情景、风险与待补充信息。
4. 以分析师身份尝试批准：页面不提供按钮；直接用 API 伪造 `X-Role` 会返回 403。右上角切换为 **审批负责人**。
5. 审批页先展示 **待执行的模拟动作**：目标 SKU、参考日预算、模拟新预算。填写审批意见并 **批准建议**；然后 **模拟执行已批准动作**。结果显示 `SIMULATION`、预算预览、外部调用 0 次。
6. 再次点击 **再次请求 · 验证幂等**。返回同一执行 ID，并提示“命中幂等”，数据库没有第二条执行记录。
7. 打开 **审计日志**：在“请求 ID”输入框精确过滤，追踪分析、批准、开始执行、成功执行、重复回放和被拦截请求。
8. 拒绝分支：启动另一次分析，在审批中心填写原因并拒绝；建议进入终态，无法再批准或执行。

演示身份（公开、仅本地）：

| 身份 | Bearer token | 权限 |
|---|---|---|
| 经营分析师 | `demo-analyst` | 查看、发起分析、初始化数据 |
| 审批负责人 | `demo-approver` | 上述权限 + 审批/拒绝 + 模拟执行 |

这是演示身份机制，**不是生产级认证**。

## 核心业务口径

金额以 USD 整数美分存储和传输，避免浮点误差。

```
净销售额 = 销售额 − 折扣 − 退款
净利润   = 净销售额 − 采购成本 − 广告支出 − 物流履约成本 − 平台手续费 − 其他可变成本
利润率   = 净利润 / 净销售额 × 100%        （分母为 0 → null）
环比     = (当前 − 上一周期) / |上一周期| × 100%（上一周期为 0 → null）
```

统计区间为 UTC 闭区间。当前周期默认最近 30 个数据日，上一周期为紧邻的等长周期；支持 7/30 天与店铺筛选，API 允许自定义最多 90 天。订单成本字段彼此独立，广告支出来自每日经营账本，不在订单中重复；退款只扣收入，采购成本已是退货后残值。每日经营记录缺失或标记不完整时，Agent 停止在 `insufficient_data`，不生成可执行建议。

完整契约见 [docs/CONTRACT.md](docs/CONTRACT.md)，设计决策见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## API 示例

```bash
# 健康检查（无认证）
curl http://127.0.0.1:8010/health

# 经营指标
curl -H "Authorization: Bearer demo-analyst" "http://127.0.0.1:8010/api/metrics?start_date=2026-10-01&end_date=2026-10-30"

# 发起分析（可选 Idempotency-Key）
curl -X POST http://127.0.0.1:8010/api/analyses \
  -H "Authorization: Bearer demo-analyst" -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-analysis-001" -d '{}'

# 批准（仅 approver，且仅 pending_approval）
curl -X POST http://127.0.0.1:8010/api/recommendations/<id>/approve \
  -H "Authorization: Bearer demo-approver" -H "Content-Type: application/json" \
  -d '{"reason":"已复核利润桥与数据完整性"}'

# 模拟执行（必须 Idempotency-Key）
curl -X POST http://127.0.0.1:8010/api/recommendations/<id>/execute \
  -H "Authorization: Bearer demo-approver" -H "Idempotency-Key: exec-001"

# 审计
curl -H "Authorization: Bearer demo-analyst" "http://127.0.0.1:8010/api/audit-logs?object_id=<id>"
```

错误格式统一为 `{"error":{"code","message","details"},"request_id":"..."}`；响应头也包含 `X-Request-ID`。`/docs` 提供 OpenAPI 文档。六个工具及其 JSON Schema 可通过 `GET /api/tools` 查询。

## 测试

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
.venv/Scripts/python.exe scripts/smoke.py          # 需服务已启动
.venv/Scripts/python.exe scripts/browser_test.py   # 安装项目本地 Chromium 后跑真实浏览器验收
```

当前实测结果：后端 33 通过（含 5 个业务评测）、前端 6 通过、生产构建通过、实际 HTTP 11 项闭环检查通过、Chromium 真实浏览器 E2E 2 通过。详情与未验证项见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)。

## 项目结构

```
backend/app/         FastAPI、模型、利润计算、工具、LangGraph、审批执行、审计
backend/tests/       单元、集成、工作流、并发、评测
backend/evals/       可重复业务评测样本
frontend/src/        React 工作台、API 类型、交互测试
frontend/e2e/        Playwright 真实浏览器验收
scripts/             一键启动、HTTP 冒烟、浏览器测试
docs/                契约、架构、验证记录、截图
compose.yaml         PostgreSQL + 后端 + 前端
AGENTS.md            开发约定
TASKS.md             交付与后续任务
```

## 已知限制

- 本地演示认证不是生产安全方案；没有租户、OIDC、权限后台、限流和密钥轮换。
- 规则引擎不调用 LLM，不会生成开放式自然语言策略；结论和文案可复现，归因是会计贡献项，不是因果证明。
- 预期影响是明确标注的条件性情景，不构成收益承诺。
- 模拟执行只写数据库预览，不改变任何真实平台。
- 超时在步骤结束后检查，不能强制中断阻塞中的同步工具。
- 无 Alembic 增量迁移；MVP 使用版本化建表和种子。
- Docker/PostgreSQL 容器运行未在当前环境验证。
- 实际 MCP Server 未实现，因此不声称支持 MCP。

## 交接与面试材料

- [docs/HANDOFF.md](docs/HANDOFF.md)：接管报告、实际验证结果、未完成项与下一步顺序。
- [docs/INTERVIEW.md](docs/INTERVIEW.md)：3 分钟演示脚本、架构讲解、高频追问与回答要点。

## 面试讲解建议

重点讲三件事：**业务闭环**（数据 → 利润桥 → 证据校验 → 人工审批 → 幂等模拟 → 审计）、**可信 Agent 设计**（确定性计算、证据绑定、数据不足不生成建议、持久化暂停点、失败可见）、**服务端安全**（角色不信任前端、条件状态更新、唯一约束与事务、请求 ID）。可以打开利润归因核对 8 项贡献之和、故意用分析师 token 调批准接口、连续两次执行观察同一执行 ID，作为现场可验证的证据。

本项目定位为 MVP，不把演示认证、规则引擎或模拟动作说成生产能力。

# ProfitOps AI

**跨境电商利润经营决策 Agent。** 销售额上涨、利润下降时，ProfitOps 从真实经营账本计算利润变化，定位会计贡献项，核验证据，生成待审批建议，经人工批准后执行**受控模拟**，并留下完整审计记录。

LangGraph + 确定性规则引擎，可选接入 **DeepSeek V4.1 Flash** 做根因解释与策略生成；没有 API Key 时自动降级为确定性规则，绝不把规则结果伪装成 LLM 输出。所有执行都明确标记 `simulation=true`，不调用真实广告、采购或付款系统。

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

### 接入 DeepSeek（可选）

不配置任何变量也能完整运行。要启用真实 LLM，在项目根目录的 `.env`（已被 Git 忽略）或后端进程环境中设置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | `deepseek` 或 `disabled` |
| `LLM_MODEL` | `deepseek-flash` | DeepSeek-V4.1-Flash-1017 的 API 模型 ID |
| `LLM_API_KEY` | 空 | 只放在本地 `.env` 或进程环境，不提交、不打印 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口根地址 |
| `LLM_TIMEOUT` | `20` | 单次请求超时秒数 |
| `LLM_MAX_TOKENS` | `4000` | 单次响应最大 token 数（256–8192） |
| `LLM_MAX_RETRIES` | `1` | 可重试错误的有界重试次数（0–3） |
| `LLM_FALLBACK_POLICY` | `rules` | `rules`：失败降级为规则；`fail`：分析失败并记录 `llm_failed` |
| `LLM_NATIVE_TOOLS` | `true` | 启用原生 Function Calling 只读工具循环 |
| `LLM_TOOL_MAX_ROUNDS` | `4` | 模型↔工具最大轮数（2–8） |
| `LLM_TOOL_MAX_CALLS` | `6` | 单次分析最大工具调用次数（1–12） |
| `LLM_TOOL_MAX_REPEATS` | `2` | 相同工具+参数允许重复次数（1–5） |
| `LLM_TOOL_BUDGET_SECONDS` | `90` | 工具循环总时间预算 |
| `LLM_TOOL_RESULT_MAX_BYTES` | `16384` | 单个工具结果回传上限（512–65536） |

DeepSeek Harness 里的 API 配置**不会**自动传给 FastAPI 后端，必须让后端进程自己读到上述变量；Docker Compose 会从项目根 `.env` 插值并注入这些变量。启用后，LangGraph 在确定性利润计算与证据校验之后调用 DeepSeek：模型可通过原生 Function Calling 请求 `get_profit_analysis` 与 `get_sku_breakdown` 两个只读工具，服务端校验名称允许列表、`policy`、`effect`、角色、输入 Schema 和本次分析范围后，用可信账本执行并把结果回传模型；写建议、审批和执行工具永远不会暴露给模型。最终输出经 Pydantic 校验、且所有 `evidence_id` 必须存在于本次确定性证据中，才允许进入待审批建议；模型只负责解释与策略，不计算利润。轮次、调用次数、重复、总预算和结果大小均有上限，失败按 `LLM_FALLBACK_POLICY` 降级。

降级状态在 API 与前端明确区分：`llm_success`、`deterministic_fallback`、`llm_failed`、`not_requested`。真实调用一次并查看模型、耗时、Token 与证据引用：

```bash
.venv/Scripts/python.exe scripts/llm_check.py
```

未配置 Key 时脚本会输出 `{"status":"skipped","reason":"LLM_API_KEY 未配置"}` 并以退出码 2 结束，不会发起网络请求。

### Docker Compose（可选 PostgreSQL）

```powershell
docker compose up --build
```

工作台 <http://127.0.0.1:8080>，API <http://127.0.0.1:8010>。Compose 使用 PostgreSQL 16 和持久卷；只绑定回环地址。已在本机 Docker Desktop 29.5.3 实际启动并通过后端与 nginx 代理各 11/11 HTTP 冒烟，详见 `docs/TEST_REPORT.md`。验收时使用 `LLM_API_KEY=`、`LLM_PROVIDER=disabled`，不会调用真实模型。

如需重新验收，可执行：

```bash
LLM_API_KEY= LLM_PROVIDER=disabled docker compose up --build -d
python scripts/smoke.py --base-url http://127.0.0.1:8010
curl -I http://127.0.0.1:8080
docker compose logs --tail=50 backend
docker compose down
```

## 演示流程（约 3 分钟）

1. 打开工作台。默认身份是“经营分析师”；总览显示销售额上涨、净利润下降，KPI、趋势图、利润归因和 SKU 明细均来自 API。
2. 点击 **启动 Profit Agent**。工作台逐步显示读取数据 → 利润计算 → 异常归因 → 证据核验 → DeepSeek 根因分析 → LLM 证据引用校验 → 创建建议 → 等待审批，每步有耗时、摘要和请求 ID。未配置 Key 时该步明确显示“已降级为确定性规则”。
3. 点击 **查看经营建议**。查看 LLM 根因/置信度/证据引用（若真实调用成功）、统计周期、上一周期、8 项利润贡献、预期影响情景、风险与待补充信息。
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

错误格式统一为 `{"error":{"code","message","details"},"request_id":"..."}`；响应头也包含 `X-Request-ID`。`/docs` 提供 OpenAPI 文档。八个工具及其 JSON Schema 可通过 `GET /api/tools` 查询。

## 测试

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
.venv/Scripts/python.exe scripts/smoke.py          # 需服务已启动
.venv/Scripts/python.exe scripts/browser_test.py   # 安装项目本地 Chromium 后跑真实浏览器验收
```

当前实测结果：后端 102 通过（含 Phase 2B 评测 Runner、负向对照与 Phase 2C 原生工具测试 28 项）、前端 8 通过、生产构建通过；Phase 2C 独立数据库 HTTP 冒烟 11/11 通过，历史阶段 Docker Compose 下后端直连与 nginx 代理各 11/11、Chromium 真实浏览器 E2E 2 通过。Phase 2C 原生 Function Calling 另执行 3 次真实脚本运行：第 1 次第 2 轮网络错误降级，第 3 次完整成功（`native_tools=true`、`final_answer`、20482.961 ms、10706 tokens、2 个只读工具调用、`all_references_valid=true`）。历史 Phase 2A/2B 真实调用 22 次，均为单次 JSON 路径；`max_tokens=4000` 的三次重复 9/9 `llm_success`、9/9 `finish_reason=stop`，当前评分器重评后广告、履约、退款均 3/3；此前 `max_tokens=3000` 的三次重复重评后为 6/9，3 次截断。详见 [docs/EVALUATION.md](docs/EVALUATION.md) 和 [docs/LLM_LIVE_CHECK.md](docs/LLM_LIVE_CHECK.md)。

## 项目结构

```
backend/app/         FastAPI、模型、利润计算、工具、LangGraph、LLM Provider、审批执行、审计
backend/tests/       单元、集成、工作流、并发、评测
backend/evals/       LLM Agent 评测样本、Runner 与报告
frontend/src/        React 工作台、API 类型、交互测试
frontend/e2e/        Playwright 真实浏览器验收
scripts/             一键启动、HTTP 冒烟、浏览器测试、真实 LLM 验证
docs/                契约、架构、验证记录、截图
compose.yaml         PostgreSQL + 后端 + 前端
AGENTS.md            开发约定
TASKS.md             交付与后续任务
```

## 已知限制

- 本地演示认证不是生产安全方案；没有租户、OIDC、权限后台、限流和密钥轮换。
- LLM 只负责解释与策略文案，利润、环比、贡献桥和动作参数始终由确定性代码计算；归因是会计贡献项，不是因果证明。真实 DeepSeek 共调用 22 次（首轮 3 次 + 退款重跑 1 次 + 3000 三次重复 9 次 + 4000 三次重复 9 次）。3000 三次重复重评后为履约 3/3、广告 2/3、退款 1/3，3 次失败均为 `finish_reason=length`；提高到 4000 后同一组三次重复为 9/9 `llm_success`，重评后广告/履约/退款均 3/3。评测结果见 [docs/EVALUATION.md](docs/EVALUATION.md)；这是 3 个合成案例上的门禁结果，不是准确率。未配置 Key 时默认走确定性降级。
- 原生 Function Calling 已用 3 次真实脚本运行验收；第 3 次完整成功，但样本很小，不能据此声明真实调用成功率或多轮稳定性。
- 预期影响是明确标注的条件性情景，不构成收益承诺。
- 模拟执行只写数据库预览，不改变任何真实平台。
- 超时在步骤结束后检查，不能强制中断阻塞中的同步工具。
- 无 Alembic 增量迁移；MVP 使用版本化建表和种子。
- Docker/PostgreSQL 容器已在本机 Docker Desktop 验证；其他机器仍需按 `docs/TEST_REPORT.md` 复验。
- 实际 MCP Server 未实现，因此不声称支持 MCP。

## 交接与面试材料

- [docs/HANDOFF.md](docs/HANDOFF.md)：接管报告、实际验证结果、未完成项与下一步顺序。
- [docs/INTERVIEW.md](docs/INTERVIEW.md)：3 分钟演示脚本、架构讲解、高频追问与回答要点。
- [docs/LLM_LIVE_CHECK.md](docs/LLM_LIVE_CHECK.md)：DeepSeek 真实调用的模型、耗时、Token、结构化输出与证据校验记录。
- [docs/EVALUATION.md](docs/EVALUATION.md)：Phase 2B Agent 评测系统、4 个合成案例、离线负向对照、真实调用与评分器修正记录。

## 面试讲解建议

重点讲三件事：**业务闭环**（数据 → 利润桥 → 证据校验 → 人工审批 → 幂等模拟 → 审计）、**可信 Agent 设计**（确定性计算、证据绑定、数据不足不生成建议、持久化暂停点、失败可见）、**服务端安全**（角色不信任前端、条件状态更新、唯一约束与事务、请求 ID）。可以打开利润归因核对 8 项贡献之和、故意用分析师 token 调批准接口、连续两次执行观察同一执行 ID，作为现场可验证的证据。

本项目定位为 MVP，不把演示认证、规则引擎或模拟动作说成生产能力。

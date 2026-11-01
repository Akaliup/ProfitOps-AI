# 验证记录

本文件记录本次交付实际执行的命令与结果。未实际执行的验证不会标记为通过。

## 2026-11-01 阶段十一验证（Claude Code）

分支 `feat/agent-evaluation-trace`，工作区包含持久化任务队列、认证与租户隔离、可观测性/限流/熔断、Alembic 基线、离线回放评测和运行手册。以下为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量（含 PostgreSQL） | `PROFITOPS_TEST_POSTGRES_URL=... python -m pytest backend/tests -q` | 215 passed, 0 failed, 0 skipped（110.94s，1 个第三方 DeprecationWarning） |
| Alembic 迁移测试 | `python -m pytest backend/tests/test_migrations.py -q` | 1 passed |
| 迁移脚本（临时 SQLite） | `python scripts/migrate.py upgrade head` / `current` / `alembic check` / `downgrade base` | 全部通过；`alembic check` 无 drift；降级后仅保留 `alembic_version` |
| PostgreSQL 知识库测试 | 一次性 `postgres:16-alpine` + `PROFITOPS_TEST_POSTGRES_URL=... pytest backend/tests/test_knowledge.py -q` | 44 passed（含 3 项 PostgreSQL 并发/索引测试） |
| 前端测试 | `npm --prefix frontend test` | 15 passed（1 个测试文件） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules |
| RAG 离线评测 | `python backend/evals/rag.py` | hit@1 83.33%、hit@3 100%、no-answer 100%、citation validity 100%，network_calls=0 |
| LLM 回放评测 | `python backend/evals/replay.py` | 4/4 案例通过，network_calls=0 |
| HTTP 冒烟 | 独立临时 SQLite + `python scripts/smoke.py --base-url http://127.0.0.1:8011` | 15/15 通过 |
| Prometheus 指标 | `curl http://127.0.0.1:8010/metrics` | 早先运行已确认；本轮未重复 |
| Python 语法检查 | `python -m py_compile`（迁移与评测脚本） | 通过 |

早先一次运行在未安装 `alembic` 时为 204 passed、4 skipped；安装 `backend/requirements.lock.txt` 后复验为 215 passed、0 skipped。PostgreSQL 使用一次性 `postgres:16-alpine` 容器与独立 schema，结束后删除，未连接或修改本机 Supabase 实例。Python 3.11 未验证（本机 `.venv` 为 3.12.8）；真实 LLM 未调用（`LLM_API_KEY` 为空）；CI 结果尚未观察，不标记为通过。迁移脚本使用临时 SQLite，未修改 `data/profitops.db`。

## 2026-10-31 Docker Compose 实际运行验收（Claude Code）

本机 Docker Desktop 29.5.3 / Compose v5.1.4（WSL2）。Docker Hub 直连受限，构建时通过本机代理拉取基础镜像并传入 BuildKit 代理参数；未修改 `compose.yaml`、Dockerfile 或业务代码。`node:22-alpine` 使用 AWS ECR Public 的官方镜像 `public.ecr.aws/docker/library/node:22-alpine` 后本地打标签。后端启动参数为 `LLM_API_KEY=`、`LLM_PROVIDER=disabled`，不调用真实 LLM。

| 检查 | 命令 | 结果 |
|---|---|---|
| 镜像构建 | `docker compose build --build-arg HTTP_PROXY=... --build-arg HTTPS_PROXY=... --build-arg NO_PROXY=...` | 后端、前端镜像构建成功 |
| 启动 | `LLM_API_KEY= LLM_PROVIDER=disabled docker compose up -d` | db/backend healthy，frontend 启动 |
| 后端冒烟 | `.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8010` | 11/11 通过 |
| nginx 代理冒烟 | `.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8080` | 11/11 通过 |
| 健康检查 | `curl http://127.0.0.1:8010/health` | `database=connected`、`simulation=true`、`llm.configured=false`、`llm.provider=disabled` |
| PostgreSQL | `docker compose exec -T db psql -U profitops_demo -d profitops -tAc 'select version();'` | PostgreSQL 16.15 |
| 前端资源 | `curl -I http://127.0.0.1:8080/assets/index-17EP4-Cu.js` | 200 |
| 容器日志 | `docker compose logs backend/frontend/db` | 无启动错误或 Traceback |

验收后执行 `docker compose down`；PostgreSQL 命名卷 `profitops-ai_profitops_pg` 保留，未删除数据。`data/profitops.db` 未被修改。

## 2026-10-31 Phase 2A：DeepSeek 接入（Claude Code）

分支 `feat/deepseek-llm-agent`，基线 `4285afe`。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 52 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，13.02s） |
| LLM/降级测试 | 同上，`backend/tests/test_llm.py` | 19 项：Provider 成功/重试/鉴权/超时/非法 JSON/截断/空响应/纯文本、环境变量读取、Schema、证据引用、无 Key 降级、失败/超时/Schema 降级、`fail` 策略、LLM 成功后审批与幂等执行 |
| 前端单元/交互 | `npm --prefix frontend test` | 8 passed（Vitest + RTL，24.46s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，14.45s |
| 实际 HTTP 闭环 | 独立数据库 `data/smoke-phase2a-final.db`、端口 8011：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过 |
| 真实浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 2/2 通过（19.8s）；后端强制 `LLM_API_KEY=''`，不消耗真实 Token |
| 真实 DeepSeek 调用 | `.venv/Scripts/python.exe scripts/llm_check.py` | **成功**：`llm_success`，模型 `deepseek-flash`，15467.974 ms，1 次尝试，4801 tokens，证据引用全部有效，退出码 0 |

真实调用详情（模型、Token、完整结构化输出、首次截断失败与修复）见 [LLM_LIVE_CHECK.md](LLM_LIVE_CHECK.md)。摘要：分析状态 `waiting_approval`，`engine=deepseek-llm-v1`，`all_references_valid=true`，未自动批准或执行。

首次真实调用在 `max_tokens=1400` 处被截断，返回 `llm_invalid_json` 并降级；修复为 `max_tokens=3000`、收紧提示词输出长度、记录 `finish_reason`，新增截断回归测试后重跑成功。失败尝试保留在验证记录中，没有伪装成成功。Phase 2B 后续把默认值改为可配置的 `LLM_MAX_TOKENS=4000`，最新评测见 `docs/EVALUATION.md`。

单元测试全部使用 `httpx.MockTransport` 或 `FakeProvider`，没有调用 DeepSeek，也没有消耗真实 Token；只有 `scripts/llm_check.py` 发起了真实调用。测试库使用临时目录或独立 `data/*.db`，未修改 `data/profitops.db`。

## 2026-11-01 Phase 2C：原生 Function Calling（Claude Code）

分支 `feat/native-function-calling`，基线 `origin/master` `1134ff4`。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 102 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，21.80s） |
| 原生工具测试 | 同上，`backend/tests/test_llm_tools.py` | 28 项：`tools`/`tool_choice` 请求、`tool_calls` 解析、多轮调用、工作流轨迹持久化、禁止写/未知/非法参数/越界工具、轮次/次数/重复/预算上限、超时与工具异常降级、最终 JSON/Schema 失败、结果截断、`rules`/`fail` 策略 |
| 前端单元/交互 | `npm --prefix frontend test` | 8 passed（Vitest + RTL，26.62s） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，13.63s |
| HTTP 冒烟 | 独立数据库 `data/smoke-phase2c.db`、端口 8011、`LLM_PROVIDER=disabled`：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过 |
| 真实 DeepSeek Function Calling | `.venv/Scripts/python.exe scripts/llm_check.py`（3 次脚本运行，真实 `deepseek-flash`） | 第 1 次第 1 轮返回 2 个工具调用并执行成功，第 2 轮 `llm_network_error` 降级；第 2 次成功但未保存完整元数据；第 3 次 `llm_success`：`native_tools=true`、`termination_reason=final_answer`、`20482.961 ms`、`10706 tokens`、2 个工具调用均成功、`all_references_valid=true` |

原生链路验证了模型返回的 `tool_calls` 会被服务端校验并执行，工具结果以 `role=tool` 回传后模型继续下一轮；也验证了 `create_recommendation`、`simulate_execute_approved_action` 等非允许工具无法执行，越界 period/store 被拒绝，循环上限和失败降级可见。第 3 次真实调用进一步确认：模型实际返回了两个允许列表内的只读工具调用，服务端执行后模型输出合法最终 JSON，全部证据 ID 有效，分析进入 `waiting_approval`，未自动批准或执行。第 1 次真实网络失败也按 `deterministic_fallback` 暴露。完整记录见 [LLM_LIVE_CHECK.md](LLM_LIVE_CHECK.md)。

测试库使用临时目录或独立 `data/*.db`，未修改 `data/profitops.db`。

## 2026-11-01 Phase 2D：Agent 评测与执行轨迹（Claude Code）

分支 `feat/agent-evaluation-trace`，基线 `origin/master` `69aae77`。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 130 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，25.17s） |
| 原生评测测试 | 同上，`backend/tests/test_native_evals.py` | 28 项：fixture 到临时账本可复现、正常多轮、多余/未知/写/审批工具、非法参数、越界、重复、轮次上限、工具失败、非法 JSON、非法证据、数据不完整、负向对照必须整次运行通过、真实 Provider 标签、真实入口授权/预算/未配置、CLI 失败退出码、integrity / expected label 失败与零案例 fail-closed |
| 原生 Tool Calling 离线评测 | `.venv/Scripts/python.exe backend/evals/runner.py native --report data/eval-tmp/native-failclosed.json` | 37 次运行：正常 3/3、负向对照 34/34、安全门禁 37/37；`dataset_passed=true`、`valid_cases=4/4`；`tool_selection_accuracy=1.0`、`tool_argument_validity_rate=0.889`、`task_completion_rate=1.0`、`evidence_reference_validity_rate=0.667`、`degradation_rate=0.833`、不必要调用 3、无效调用 24；`provider_type=fake_tools`，真实网络调用 0 |
| 前端单元/交互 | `npm --prefix frontend test -- --run` | 11 passed（Vitest + RTL，6.11s），含敏感工具参数不回显测试 |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，5.66s |
| HTTP 冒烟 | 独立数据库 `data/smoke-phase2d.db`、端口 8011、`LLM_PROVIDER=disabled`：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过，failed=0 |
| 真实浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 3/3 通过（16.4s）：真实 API 闭环、拒绝分支/手机视图、Agent Trace 桌面与 390px 移动端 |
| Agent Trace 截图 | 同上 | `docs/screenshots/agent-trace.png`、`docs/screenshots/agent-trace-mobile.png` |
| 真实 `native --real` | 未执行 | **未验证**：入口要求显式授权和调用预算，本阶段未消耗真实 DeepSeek Token |

Phase 2D 离线评测的 token 与延迟来自脚本化 `FakeToolProvider` 的固定值，不是真实模型性能；`tool_selection_accuracy` 只统计 3 次正常场景，不包含负向对照。真实浏览器 Trace 测试使用 Playwright 路由注入一条已记录的原生失败轨迹，验证前端渲染与 390px 布局，不代表真实模型调用。

fail-closed 回归：复制案例并破坏 `source_type`、`expected.primary_driver`，或使用空案例目录时，CLI 均返回 1；报告 `dataset_checks.failed_cases` 记录 `case_id`、失败阶段和具体失败检查项。前端测试注入 `api_key`、`Authorization`、`token`、`password` 及非法 `period` / `store_id`，断言页面文本不包含其值，同时保留轮次、成功/失败和耗时展示。

测试数据库为临时目录或独立 `data/*.db`，未修改 `data/profitops.db`；`data/smoke-phase2d.db` 与 `data/e2e.db` 均在 `data/` 下且被 Git 忽略。

## 2026-11-01 RAG 运营知识库（Claude Code）

分支 `feat/agent-evaluation-trace`，在 Phase 2D 工作树上增量实现。以下均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 142 passed，0 failed，0 skipped（1 个第三方 DeprecationWarning，34.18s） |
| 知识库测试 | 同上，`backend/tests/test_knowledge.py` | Seed 幂等、相关/无关检索、`no_answer` 清空 hits、approver 创建/停用、analyst 403、重复正文幂等、引用子集校验、提示词隔离、Markdown 切分、OpenAI 兼容 embedding 排序/归一化与 502 失败映射 |
| 前端单元/交互 | `npm --prefix frontend test -- --run` | 15 passed（Vitest + RTL，9.66s），含知识库检索、无答案状态、approver 新增文档、建议引用标记、LLM 知识引用 |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，2252 modules，4.90s |
| RAG 离线评测 | `.venv/Scripts/python.exe backend/evals/rag.py` | 6 篇文档、8 个查询；hit@1 83.33%、hit@3 100%、no-answer 100%、citation validity 100%；网络调用 0 |
| HTTP 冒烟 | 独立数据库 `data/smoke-rag-20261101.db`、端口 8011、`LLM_PROVIDER=disabled`、空 `LLM_API_KEY`：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 15/15 通过，failed=0；新增知识库 Seed、相关检索、无答案、analyst 写入 403 四项检查 |
| 真实浏览器 E2E | 未执行 | **未验证**：本次未重新运行 Playwright；知识库页面只由 Vitest + RTL 和生产构建覆盖 |
| 真实 embedding 服务 | 未执行 | **未验证**：默认 hashing embedding；OpenAI 兼容 Provider 只用 `httpx.MockTransport` 测试，没有连接真实 Ollama/vLLM/云端端点 |

RAG 评测报告写入 `backend/evals/reports/rag-latest.json`。评测强制 hashing embedding，使用临时 SQLite，不读写 `data/profitops.db`；`hit@1` 未达 100% 的查询是“执行幂等键重复请求怎么办”，top-1 命中广告预算文档、top-2 命中目标文档，报告保留真实排名，没有为提升 hit@1 调参。

开发过程中发现并修复：无关查询“如何预订会议室午餐”最初因中文单字“预/订”误命中广告文档；修复为单字低权重、`KNOWLEDGE_MIN_SCORE` 默认 0.15，并在 `no_answer` 时清空 hits。HTTP 冒烟使用独立 `data/smoke-rag-20261101.db`，未修改 `data/profitops.db`。

## 已验证（2026-10-30 基线）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 33 passed, 0 failed, 0 skipped（1 个第三方 DeprecationWarning） |
| 业务评测 | 同上，`backend/tests/test_evals.py` | 5/5 样本通过：广告驱动、健康增长、履约压力、零基数、退款压力 |
| 前端单元/交互 | `npm --prefix frontend test` | 6 passed, 0 failed（Vitest + RTL） |
| TypeScript + 生产构建 | `npm --prefix frontend run build` | 通过；Vite 6.4.5，图表拆分为懒加载 chunk |
| 前端依赖审计 | `npm --prefix frontend audit --registry=https://registry.npmjs.org` | 0 vulnerabilities |
| Python 依赖一致性 | `.venv/Scripts/python.exe -m pip check` | No broken requirements |
| 一键完整启动 | `.venv/Scripts/python.exe scripts/dev.py`（含 pip 与 `npm ci`） | 安装完成；前端 200，API health 正常；Vite 6.4.5 / Uvicorn 已启动 |
| 实际 HTTP 闭环 | `.venv/Scripts/python.exe scripts/smoke.py` | 11/11 检查通过（空库自动初始化后复跑仍为 11/11） |
| 真实浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 2/2 通过：桌面完整闭环、手机视图与拒绝分支；无页面异常；使用隔离的 `data/e2e.db` |
| 容器配置语法 | `docker compose config --quiet` | 通过 |

## 2026-10-30 接管复验（Claude Code）

接管后重新执行以下命令，结果均为本次实际输出：

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 33 passed，1 个第三方 DeprecationWarning |
| 前端测试 | `npm --prefix frontend test` | 6 passed |
| 生产构建 | `npm --prefix frontend run build` | 通过 |
| 一键启动 | `python scripts/dev.py --skip-install` | 启动成功；`/health` 200、前端 200、summary `anomaly=true` / `bridge_verified=true`；Ctrl+C 后 8010/5180 端口释放 |
| HTTP 冒烟 | 独立数据库 + 8011 端口：`.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8011` | 11/11 通过 |
| 浏览器 E2E | `.venv/Scripts/python.exe scripts/browser_test.py --skip-install` | 2/2 通过，截图已刷新 |
| Python 依赖 | `.venv/Scripts/python.exe -m pip check` | No broken requirements |
| 前端依赖 | `npm --prefix frontend audit --registry=https://registry.npmjs.org` | 0 vulnerabilities |
| Compose 语法 | `docker compose -f compose.yaml config --quiet` | 通过 |
| Compose 运行 | `docker compose up --build` | 当时未执行：Docker Desktop 引擎不可用；2026-10-31 已补验（见上） |

新增回归覆盖：工具 `policy` 分类与 `deny` 拒绝、API 时间戳显式 UTC、审计按 `request_id` 过滤、Seed 不删除审批/执行/审计、审批页动作预览与审计过滤 UI。冒烟与浏览器测试使用独立数据库（`data/smoke-takeover-2.db`、`data/e2e.db`），未修改 `data/profitops.db` 中的用户运行数据。

## 尚未验证（环境阻碍）

- Docker Compose 已于 2026-10-31 在本机 Docker Desktop 29.5.3 上完成 PostgreSQL 16、后端和 nginx 前端的实际启动验收；后端直连与 nginx 代理各 11/11 通过。其他机器仍需按本文件复验；本机 Docker Hub 直连受限，重新构建时可能需要代理或镜像加速。
- Phase 2C 原生 Function Calling 已对真实 DeepSeek 执行 3 次脚本运行：第 1 次因网络错误降级，第 3 次完整成功并记录了 `tool_calls`、逐轮 Token/耗时和最终输出。样本只有 3 次，不能据此声明真实调用成功率或多轮稳定性。
- Phase 2D 的 `native --real` 真实模型工具评测未执行；当前 37 次结果全部来自 `FakeToolProvider`，只能证明生产工具循环与门禁在合成案例上的行为，不能声明真实模型的工具选择准确率、参数合法率、真实 token 或延迟。
- RAG 的真实 embedding 服务与真实业务语料未验证：默认 hashing embedding 是离线方案，OpenAI 兼容 Provider 只有 MockTransport 测试；6 篇合成 SOP、8 个查询的评测不能声明通用语义检索准确率。PDF/OCR、reranker、文档级 ACL、大规模向量索引未实现。
- 本次 RAG 改动未重新运行 Playwright 浏览器 E2E；知识库页面由 Vitest + RTL 和生产构建覆盖，未在真实浏览器中复验。

浏览器验收不属于未验证项：Playwright + Chromium 真实页面测试 2/2 通过，截图位于 `docs/screenshots/`。

## 环境

Windows 11、Python 3.12.8（venv）、Node 24.16.0、npm 11.13.0、Git 2.40.0、Docker Desktop 29.5.3 / Compose v5.1.4（2026-10-31 已启动并完成容器验收）。默认端口 8010 / 5180；本机 8000 / 5173 被其他进程占用，本项目不修改它们。

## 开发过程中的失败与修复

- 首轮后端测试 28 passed / 1 failed：`test_no_anomaly_does_not_invent_action` 所选的 7 天数据实际也存在轻微利润背离，测试前提错误。改为显式构造无成本、利润随销售增长的数据后重跑通过。没有放宽生产逻辑。
- 首次 npm 安装因本机镜像 TLS `ECONNRESET` 失败，改用官方 registry 后成功；随后升级 Vite/Vitest/Playwright 修复审计漏洞，最终 `npm audit` 为 0。
- 首次 Playwright WebServer 启动失败：Windows 将 Python 相对路径拆成命令；改为绝对路径并加引号后，浏览器测试通过。
- Docker Desktop 启动在 120 秒限制内未就绪，停止尝试；因此当时的容器运行验证保持未完成状态。2026-10-31 再次启动 Docker Desktop 后成功完成 Compose 验收；Docker Hub 直连仍受限，构建时使用本机代理。

最终结果不包含任何伪造或跳过的测试。

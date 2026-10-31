# 验证记录

本文件记录本次交付实际执行的命令与结果。未实际执行的验证不会标记为通过。

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

浏览器验收不属于未验证项：Playwright + Chromium 真实页面测试 2/2 通过，截图位于 `docs/screenshots/`。

## 环境

Windows 11、Python 3.12.8（venv）、Node 24.16.0、npm 11.13.0、Git 2.40.0、Docker Desktop 29.5.3 / Compose v5.1.4（2026-10-31 已启动并完成容器验收）。默认端口 8010 / 5180；本机 8000 / 5173 被其他进程占用，本项目不修改它们。

## 开发过程中的失败与修复

- 首轮后端测试 28 passed / 1 failed：`test_no_anomaly_does_not_invent_action` 所选的 7 天数据实际也存在轻微利润背离，测试前提错误。改为显式构造无成本、利润随销售增长的数据后重跑通过。没有放宽生产逻辑。
- 首次 npm 安装因本机镜像 TLS `ECONNRESET` 失败，改用官方 registry 后成功；随后升级 Vite/Vitest/Playwright 修复审计漏洞，最终 `npm audit` 为 0。
- 首次 Playwright WebServer 启动失败：Windows 将 Python 相对路径拆成命令；改为绝对路径并加引号后，浏览器测试通过。
- Docker Desktop 启动在 120 秒限制内未就绪，停止尝试；因此当时的容器运行验证保持未完成状态。2026-10-31 再次启动 Docker Desktop 后成功完成 Compose 验收；Docker Hub 直连仍受限，构建时使用本机代理。

最终结果不包含任何伪造或跳过的测试。

# 验证记录

本文件记录本次交付实际执行的命令与结果。未实际执行的验证不会标记为通过。

## 已验证

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
| Compose 运行 | `docker compose up --build` | 未执行：Docker Desktop 引擎仍不可用 |

新增回归覆盖：工具 `policy` 分类与 `deny` 拒绝、API 时间戳显式 UTC、审计按 `request_id` 过滤、Seed 不删除审批/执行/审计、审批页动作预览与审计过滤 UI。冒烟与浏览器测试使用独立数据库（`data/smoke-takeover-2.db`、`data/e2e.db`），未修改 `data/profitops.db` 中的用户运行数据。

## 尚未验证（环境阻碍）

- Docker Compose 实际启动 PostgreSQL、后端和前端：本机 Docker Desktop 引擎不可用。`docker desktop start` 在 120 秒后仍无法连接 `dockerDesktopLinuxEngine`，启动尝试被停止。因此不能声明容器运行、PostgreSQL 并发或全新容器数据库初始化已经通过。SQLite 并发测试已通过，Compose 配置只验证了语法。

浏览器验收不属于未验证项：Playwright + Chromium 真实页面测试 2/2 通过，截图位于 `docs/screenshots/`。

## 环境

Windows 11、Python 3.12.8（venv）、Node 24.16.0、npm 11.13.0、Git 2.40.0、Docker CLI 29.5.3（引擎未运行）。默认端口 8010 / 5180；本机 8000 / 5173 被其他进程占用，本项目不修改它们。

## 开发过程中的失败与修复

- 首轮后端测试 28 passed / 1 failed：`test_no_anomaly_does_not_invent_action` 所选的 7 天数据实际也存在轻微利润背离，测试前提错误。改为显式构造无成本、利润随销售增长的数据后重跑通过。没有放宽生产逻辑。
- 首次 npm 安装因本机镜像 TLS `ECONNRESET` 失败，改用官方 registry 后成功；随后升级 Vite/Vitest/Playwright 修复审计漏洞，最终 `npm audit` 为 0。
- 首次 Playwright WebServer 启动失败：Windows 将 Python 相对路径拆成命令；改为绝对路径并加引号后，浏览器测试通过。
- Docker Desktop 启动在 120 秒限制内未就绪，停止尝试；因此容器运行验证保持未完成状态。

最终结果不包含任何伪造或跳过的测试。

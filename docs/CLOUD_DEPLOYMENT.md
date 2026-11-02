# ProfitOps AI 云端单机部署说明

本文面向一台 Linux 云主机（Windows 11 通过 SSH 操作）的单机 Docker Compose 部署。所有业务执行仍是 simulation：不会调用真实广告、采购或支付系统；本轮不配置 DeepSeek API Key，LLM 固定为 `disabled`，分析由确定性规则引擎完成。

> 本轮没有云服务器凭据，**没有实际部署到公网**。本文的“已验证”仅指本机隔离 Compose 栈的验收；云端步骤是可直接执行的准备方案，不是已完成的生产部署。

## 1. 拓扑

```text
Internet / 演示访问者
        │  HTTPS 443（唯一发布端口）
        ▼
┌──────────────────────────────────────────────┐
│ frontend（React 静态文件 + Nginx TLS 终止）   │
│  /             Basic Auth + SPA              │
│  /api/demo     Basic Auth（返回演示身份）     │
│  /api/auth/login Basic Auth                  │
│  /api/*        Bearer only → backend:8000    │
└──────────────────────┬───────────────────────┘
                       │ Compose 私有网络
                       ▼
┌──────────────────────────────────────────────┐
│ backend（FastAPI，不启动内置 worker）          │
│ PROFITOPS_ANALYSIS_WORKER_ENABLED=false       │
└──────────────────────┬───────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
┌───────────────┐             ┌────────────────┐
│ worker        │             │ db             │
│ python -m     │             │ PostgreSQL 16  │
│ app.worker    │             │ 持久卷          │
└───────────────┘             └────────────────┘

init（一次性）：alembic upgrade head && python -m app.init
backend / worker 等 init 成功后才启动
```

只有 `frontend` 发布宿主机端口；`backend`、`worker`、`db` 均只在 Compose 网络内监听，不映射到宿主机。

## 2. 相关文件

| 文件 | 作用 |
|---|---|
| `compose.yaml` | 本地开发基线：PostgreSQL、后端、nginx 前端 |
| `compose.cloud.yaml` | 云端覆盖：关闭 API 内 worker、增加独立 worker 与一次性 init、只发布 HTTPS |
| `frontend/nginx.cloud.conf` | TLS、Basic Auth、反向代理和 SPA 路由 |
| `backend/app/init.py` | 一次性建表/种子入口，避免 API 与 worker 首次启动竞态 |
| `scripts/cloud_prepare.py` | 生成 `.env.cloud`、htpasswd 和自签名证书（或校验已有证书） |
| `.gitignore` | 忽略 `.env.*`、`deploy/auth*/`、`deploy/tls*/`，防止密钥进入 Git |

## 3. 安全模型

### 3.1 两层访问控制

1. **Nginx Basic Auth（独立访问门）**：保护 `/`、`/api/demo`、`/api/auth/login`。没有访问口令的人连 SPA 和演示身份引导都拿不到。
2. **应用 Bearer token**：保护 `/api/*` 业务接口。前端从 `/api/demo` 取得当前演示身份 token，再以 `Authorization: Bearer ...` 调用业务 API。

`/api/` 故意不叠加 Basic Auth。HTTP Basic 和 Bearer 都使用 `Authorization` 请求头；如果 Nginx 在 `/api/` 上强制 Basic，会覆盖或拒绝 SPA 发出的 Bearer 请求。当前设计让 Nginx 只做 TLS 与转发，业务鉴权仍由 FastAPI 完成。

### 3.2 演示身份不是正式认证

- `PROFITOPS_DEMO_MODE=true` 时 `/api/demo` 返回 analyst / approver 两个演示 token。
- token 由 `scripts/cloud_prepare.py` 生成 48 位随机十六进制值，写入不进入 Git 的 `.env.cloud`；不是仓库里的公开常量。
- `/api/demo` 在 Basic Auth 之后，未通过访问门的人无法取得 token。
- 这仍是演示身份，不是 OIDC/SSO/MFA，不能当作正式用户认证或生产 IAM。

### 3.3 密钥与证书

- `PROFITOPS_AUTH_SECRET`、数据库密码、两个演示 token、两个演示密码、Basic Auth 密码均由脚本随机生成。
- 不使用 `dev-only-change-me` 默认 JWT 密钥。
- `.env.cloud`、`deploy/auth*/`、`deploy/tls*/` 已被 `.gitignore` 忽略；不要提交、不要贴到聊天或日志。
- 本地验证用自签名证书；云端必须换成受信任证书（`--tls-mode existing`，把 `fullchain.pem` 和 `privkey.pem` 放到 TLS 目录）。
- 文件权限：`.env.cloud` 与 `privkey.pem` 应为 `600`；`htpasswd` 不应被 Web 静态目录直接访问。

## 4. Windows 11 通过 SSH 部署

以下命令在 Windows 11 的 PowerShell 或 Windows Terminal 中执行；`<...>` 为占位符。

### 4.1 连接与准备

```powershell
ssh <user>@<server-ip>
```

服务器需要 Docker Engine 24+ 与 Docker Compose v2（本机验证版本为 Docker 29.5.3 / Compose 5.1.4）。若服务器是 Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin openssl git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

重新登录使 docker 组生效。把仓库放到服务器（示例 `/opt/profitops`）：

```bash
sudo mkdir -p /opt/profitops
sudo chown "$USER":"$USER" /opt/profitops
git clone <repository-url> /opt/profitops
cd /opt/profitops
git checkout feat/final-agent-validation
```

### 4.2 生成非 Git 配置

本地验证（自签名证书，只绑定回环地址）：

```bash
python3 scripts/cloud_prepare.py \
  --env-file .env.cloud \
  --auth-dir deploy/auth \
  --tls-dir deploy/tls \
  --domain localhost \
  --https-bind 127.0.0.1 \
  --https-port 8443
```

云端（已有受信任证书，绑定全部地址的 443）：

```bash
python3 scripts/cloud_prepare.py \
  --env-file .env.cloud \
  --auth-dir deploy/auth \
  --tls-dir deploy/tls \
  --domain demo.example.com \
  --https-bind 0.0.0.0 \
  --https-port 443 \
  --tls-mode existing
```

`--tls-mode existing` 要求 `deploy/tls/fullchain.pem` 与 `deploy/tls/privkey.pem` 已存在。脚本会打印 Basic Auth 用户名和密码；只通过安全渠道交给演示访问者，不要写进 PR、聊天记录或截图。

检查生成结果（不要输出密码）：

```bash
ls -l .env.cloud deploy/auth/htpasswd deploy/tls/
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud config --quiet
```

### 4.3 防火墙 / 安全组

只放行：

- `22/tcp`（SSH，最好限制来源 IP）
- `443/tcp`（HTTPS 演示入口）

不要放行 `8000`、`8010`、`5432`、`8080`。Compose 本身没有发布这些端口，防火墙是第二道保险。

## 5. 首次初始化与迁移

云端覆盖把初始化拆成一次性 `init` 服务：

```text
alembic -c alembic.ini upgrade head && python -m app.init
```

`backend` 与 `worker` 都依赖 `init` 成功完成（`service_completed_successfully`），所以不会出现 API 和 worker 同时抢建表/抢种子版本的情况。

首次启动：

```bash
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud up -d --build
```

查看初始化日志：

```bash
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud logs init
```

首次成功应看到：

```text
Running upgrade  -> 0001_baseline, Baseline schema for a fresh ProfitOps AI database.
Running upgrade 0001_baseline -> 0002_tenant_scoped_idempotency, ...
{'seeded': True, 'version': 'identity-v1', 'users': 2, ...}
{'seeded': True, 'version': 'demo-60d-v1', 'days': 60, ...}
{'seeded': True, 'version': 'knowledge-v1', 'documents': 6, ...}
```

重复执行 `init` 是安全的：种子按版本号判断，已存在时返回 `seeded: False`，不会删除审批、执行或审计数据。

手动检查迁移版本：

```bash
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud \
  exec backend alembic -c alembic.ini current
```

手动再跑一次初始化（例如修复种子失败）：

```bash
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud \
  run --rm init
```

### 5.1 首次启动并发说明

本轮实测：在**全新 SQLite 库**上让两个线程同时调用 `initialize_database`，可能有一个线程报 `OperationalError: table users already exists`；schema 已存在后并发初始化是安全的。PostgreSQL 的 DDL 同样不应由多个应用进程同时执行。

因此部署约束是：**新库必须先由一次性 `init` 完成 Alembic 与种子，再启动 API/worker**。`compose.cloud.yaml` 已用依赖条件保证这一点；不要绕过 `init` 直接对空库启动多个 backend/worker。

## 6. 启动、停止与状态

```bash
# 启动/更新
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud up -d --build

# 状态与健康
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud ps

# 日志（json-file，10MB × 3，已配置轮转）
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud logs -f --tail=100 backend worker frontend

# 只停 worker，任务保留 queued
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud stop worker

# 恢复 worker，继续消费 queued
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud start worker

# 重启 API，不影响独立 worker
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud restart backend

# 停止整套栈，保留 PostgreSQL 持久卷
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud down

# 危险：删除容器和持久卷，数据不可恢复；生产禁止
# docker compose ... down -v
```

`down` 不带 `-v` 时不会删除命名卷；不要对含审批/审计数据的栈执行 `down -v`。

## 7. 数据库备份与恢复

### 7.1 逻辑备份

先加载非 Git 环境变量（不要在日志中回显密码）：

```bash
set -a; . ./.env.cloud; set +a
```

```bash
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud \
  exec -T db pg_dump -U "${PROFITOPS_POSTGRES_USER:-profitops}" "${PROFITOPS_POSTGRES_DB:-profitops}" \
  > "profitops-$(date +%Y%m%d-%H%M%S).sql"
```

建议每天备份到栈外目录，并保留至少 7 天；备份文件包含业务数据，按敏感数据保管。

### 7.2 恢复

先在隔离项目/临时库验证备份，不要直接覆盖正式库。恢复前停止写入：

```bash
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud stop backend worker
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud \
  exec -T db psql -U "${PROFITOPS_POSTGRES_USER:-profitops}" -d "${PROFITOPS_POSTGRES_DB:-profitops}" \
  < profitops-YYYYmmdd-HHMMSS.sql
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud start backend worker
```

恢复后检查：

```bash
set -a; . ./.env.cloud; set +a
curl -k -u "$PROFITOPS_ACCESS_USER:$PROFITOPS_ACCESS_PASSWORD" \
  https://127.0.0.1/api/demo
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud ps
```

### 7.3 卷级备份

命名卷由 Compose 项目名决定（默认 `profitops-cloud_profitops_pg`）。卷级快照必须在停库后做，且恢复时目标卷必须为空；跨主机恢复优先使用 `pg_dump`，更不易出错。

## 8. 演示地址与安全访问方案

### 8.1 本地验证

- 地址：`https://127.0.0.1:8443/`
- 证书：自签名，浏览器会提示不受信任；仅用于本机验收。
- Basic Auth：`.env.cloud` 中的 `PROFITOPS_ACCESS_USER` / `PROFITOPS_ACCESS_PASSWORD`。
- 页面右上角可切换“经营分析师 / 审批负责人”；运维看板仅审批负责人可见。

### 8.2 云端演示

- 地址：`https://<domain>/`
- 证书：受信任证书（`--tls-mode existing`）。
- 访问：先输入 Basic Auth，再进入 SPA；演示 token 由 `/api/demo` 在 Basic 之后返回，不公开。
- 建议把安全组/防火墙来源限制为演示者 IP；演示结束后 `docker compose ... down` 停止服务，保留卷。
- 不要把 Basic 密码、演示 token、`.env.cloud` 或数据库备份发到公开渠道。
- 演示身份不是正式认证；若需要正式用户体系，应接入 OIDC/SSO、MFA 和密钥管理，不能复用这套 demo token。

### 8.3 轮换

```bash
python3 scripts/cloud_prepare.py --env-file .env.cloud --auth-dir deploy/auth \
  --tls-dir deploy/tls --domain <domain> --https-bind 0.0.0.0 --https-port 443 \
  --tls-mode existing --force
docker compose -f compose.yaml -f compose.cloud.yaml --env-file .env.cloud up -d
```

`--force` 会轮换数据库密码、JWT 密钥、演示 token、演示密码和 Basic 密码；已有登录会话失效，需重新进入。轮换后确认旧密码不再可用。

## 9. 故障排查

| 现象 | 检查 | 处理 |
|---|---|---|
| frontend 不健康 | `docker compose ... logs frontend` | 确认 `deploy/tls/fullchain.pem`、`privkey.pem`、`deploy/auth/htpasswd` 存在且容器可读；确认 443 未被占用 |
| 浏览器 401 | 是否输入 Basic Auth；`/api/demo` 是否也 401 | 用 `curl -k -u ... https://host/api/demo` 验证；检查 htpasswd 与 `.env.cloud` 的访问密码是否一致 |
| SPA 能开但业务 API 401 | `/api/` 的 Bearer token 是否缺失/过期 | 刷新页面重新从 `/api/demo` 取 token；检查 `PROFITOPS_ANALYST_TOKEN` / `PROFITOPS_APPROVER_TOKEN` 是否为空或相同 |
| init 失败 | `logs init` | 常见为数据库密码不一致、Alembic 迁移失败、种子约束冲突；先备份再处理，不要清库 |
| backend 不健康 | `logs backend` | 检查 `PROFITOPS_DATABASE_URL`、数据库健康、Alembic 是否已到 head |
| worker 不消费 | `logs worker`、`ps` | 确认 worker 容器在运行、与 backend 使用同一 `PROFITOPS_DATABASE_URL`；确认任务状态为 `queued` 而不是 `failed` |
| 任务一直 queued | worker 是否被 `stop` | `start worker`；查看 `analysis.recovered` / `analysis.retry_scheduled` 审计 |
| 数据库重启后 API 报错 | backend 健康与日志 | 等 db healthy、backend 重连；连接池会自动恢复，必要时 `restart backend` |
| 端口冲突 | `ss -lntp` / `docker ps` | 只应看到 443（以及 SSH 22）；修改 `PROFITOPS_HTTPS_PORT` 或释放占用 |
| 日志占满磁盘 | `docker compose ... logs` | 已配置 json-file `max-size=10m`、`max-file=3`；仍建议监控磁盘 |

## 10. 本地 Compose 验收（2026-11-02）

### 10.1 环境

| 项目 | 值 |
|---|---|
| 项目名 | `profitops-cloud-verify`（隔离临时栈） |
| Docker / Compose | 29.5.3 / 5.1.4 |
| 数据库 | PostgreSQL 16 Alpine，命名卷 `profitops-cloud-verify_profitops_pg` |
| 后端 | FastAPI 容器，`PROFITOPS_ANALYSIS_WORKER_ENABLED=false` |
| Worker | 同一后端镜像，`python -m app.worker` |
| 前端 | Nginx 1.27 + React 构建产物，`127.0.0.1:8443→443` |
| LLM | `LLM_PROVIDER=disabled`，`LLM_API_KEY` 为空，未调用付费模型 |
| 正式数据库 | 未读写；验收使用独立临时栈与独立卷 |

### 10.2 十项验收

| # | 验收项 | 结果 | 证据 |
|---:|---|---|---|
| 1 | 创建 queued 任务 | **PASS** | `POST /api/analyses` 返回 202、`status=queued`；ID `3b4dc33c-...` |
| 2 | FastAPI 不执行任务 | **PASS** | worker 停止 3 秒后仍为 `queued`，`steps=0`、`recommendation_id=null` |
| 3 | 独立 worker 完成任务 | **PASS** | `start worker` 后变为 `waiting_approval`，`attempts=1` |
| 4 | 重启 API 不影响 worker | **PASS** | `restart backend` 前后 worker `StartedAt` 不变；新任务仍完成 |
| 5 | 关闭 worker 后任务保留 queued | **PASS** | 停止 worker 后新任务 ID `0ca20b98-...` 保持 `queued` |
| 6 | 恢复 worker 后继续处理 | **PASS** | `start worker` 后同一任务变为 `waiting_approval` |
| 7 | 人工审批、模拟执行、幂等、审计 | **PASS** | `recommendation.approve`、`execution.executed`、`execution.replayed`；同一 `execution_id=99427e36-...`，`simulation=true`，`replayed=true` |
| 8 | 前端可访问运维看板 | **PASS** | Chromium 通过 HTTPS + Basic Auth 打开；页面含“任务队列”“任务总数”“模型与工具”“Token 与成本”“错误与人工环节”“已执行建议”；`consoleErrors=[]` |
| 9 | 数据库重启后记录保留 | **PASS** | `restart db` 后 `jobs.total=6`、`executions.total=2` 不变；已完成分析仍为 `executed` |
| 10 | 仅允许的监听端口 | **PASS** | 仅 `profitops-cloud-verify-frontend-1 127.0.0.1:8443->443/tcp`；backend/db/worker 无宿主机发布端口 |

补充检查：

- 未带 Basic Auth 请求 `/api/demo` 返回 **401**。
- 首次 `init` 日志确认 `0001_baseline`、`0002_tenant_scoped_idempotency`，种子 2 个用户、60 天数据、6 篇知识文档。
- 运维聚合 `jobs.by_status` 各项之和等于 `jobs.total`；本轮 `jobs.total=6`、`executed=2`、`executions.total=2`。
- 验收截图：[cloud-compose-ops.png](screenshots/final-demo/cloud-compose-ops.png)。

### 10.3 PASS / FAIL / SKIP / BLOCKED

| 项目 | 状态 | 说明 |
|---|---|---|
| 本地 Compose 单机栈 | **PASS** | 5 个服务（db/init/backend/worker/frontend）实际启动，健康检查通过 |
| 首次迁移与种子 | **PASS** | Alembic 0001/0002 + `app.init` 首次执行成功；重复执行幂等 |
| 独立 worker 队列 | **PASS** | queued → worker claim → waiting_approval；API 不内联执行 |
| API/worker 分离 | **PASS** | 重启 API 不影响 worker；停止 worker 任务保留 |
| 审批/执行/幂等/审计 | **PASS** | 同键重放同一 execution_id；审计含 approved/executed/replayed |
| 前端运维看板 | **PASS** | HTTPS + Basic + Chromium 实测，无 console/page error |
| 数据库持久化 | **PASS** | 数据库重启后记录数与状态保持 |
| 端口最小暴露 | **PASS** | 仅 frontend 443（本机 8443）发布 |
| 真实云服务器部署 | **SKIP / BLOCKED** | 本轮没有云服务器凭据，未执行；只完成准备与本机验收 |
| 真实 DeepSeek | **SKIP** | 按约束禁用，未调用、未计费 |
| BGE-M3 真实检索 | **BLOCKED（沿用既有结论）** | 本轮使用 hashing embedding，不重新评估 |
| 多副本 / Kubernetes / Redis / Celery | **SKIP** | 明确不在本轮范围；当前单机单 API + 单 worker |
| 真实广告/采购/支付副作用 | **PASS（无）** | 所有执行 `simulation=true`、`external_calls=0` |

## 11. 已知限制

- 单机 Compose，不是高可用；API、worker、数据库都是单实例。
- 限流桶和指标注册表是进程内状态，不能跨副本聚合；多副本需要共享限流/指标后端。
- Basic Auth 只是演示访问门，不是正式 IAM；没有 OIDC/SSO、MFA、密码重置、密钥托管。
- 自签名证书只用于本地；云端必须换受信任证书。
- 没有自动备份调度；需要运维侧定时 `pg_dump` 并验证恢复。
- `.env.cloud` 是主机文件，不是 Secret Manager；生产应使用云密钥管理或容器 Secret。
- worker 的健康检查只验证 PID 1 存活，能发现进程退出并由 `restart: unless-stopped` 拉起；如果 worker 内部循环卡死但进程仍在，健康检查不会发现。需要更强探针时应在后续版本增加心跳文件或 HTTP 管理端口。
- LLM 固定禁用；若未来启用，必须单独授权、记录调用次数，并确保密钥不进入 Git/镜像/日志。

## 12. 回滚

1. 先备份数据库（见第 7 节）。
2. 停止栈但保留卷：`docker compose ... down`（不要 `-v`）。
3. 切换到上一个已验证提交或镜像标签，重新 `up -d --build`。
4. 若新版本已执行不可逆迁移，按备份恢复；不要手工删除 `alembic_version` 或业务表。
5. 回滚后检查 `/health`、`/api/demo`、`/api/ops/summary` 和 worker 日志。

## 13. 结论

- 本地隔离 Compose 栈的十项验收全部 PASS；首次迁移、独立 worker、API/worker 分离、审批幂等、审计、前端看板、数据库重启持久化和端口最小暴露均有实际运行证据。
- 云端单机部署配置、初始化、备份、故障排查和访问控制方案已准备完成。
- **未实际部署到云服务器**：本轮没有云服务器凭据，不能声明“云端部署完成”。实际云端上线仍需在目标主机执行第 4–6 节并重新验收。

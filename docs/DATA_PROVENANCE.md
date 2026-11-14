# 数据来源与边界

> 结论先行：仓库内没有真实商户经营数据。经营账本、知识库、RAG 查询集和 LLM 评测案例全部是程序生成的合成数据；真实发生的是代码执行、测试、浏览器操作、API 调用，以及可选的 DeepSeek / embedding 网络调用。因此本项目的验证结果只能说明软件行为与契约，不能说明真实经营效果、真实检索准确率或模型准确率。

## 1. 数据清单

| 数据 | 生成位置 | 规模 | 用途 | 真实业务数据 |
|---|---|---|---|---|
| 演示经营账本 | `backend/app/seed.py` | 2 店铺 × 3 SKU × 60 天；1,920 条订单、360 条每日经营记录 | 总览、Agent 分析、审批/执行、审计、截图、演示与 e2e | 否，确定性合成 |
| 演示身份 | `backend/app/seed.py` `seed_identity` | 1 个租户、2 个账号（analyst / approver） | 本地角色演示 | 否 |
| 知识库 | `backend/app/knowledge.py` `SEED_DOCUMENTS` | 6 篇合成 SOP、32 个 chunk，来源前缀 `synthetic://` | RAG 检索、拒答、引用校验 | 否，合成 |
| RAG 校准/测试查询 | `backend/evals/rag_calibration.json`、`backend/evals/rag_test.json` | 校准 26 条、测试 24 条；两集合 id 与 query 文本不重叠，测试集含 6 条跨文档和 4 条不可回答 | 校准阈值与排序；测试集冻结回归 | 否，人工编写的合成查询 |
| 历史 RAG 查询 | `backend/evals/rag_queries.json` | 42 条（36 可回答 + 6 不可回答），旧统一集合 | 历史对照；曾同时用于调参与汇报，不再作为独立测试集 | 否，人工编写的合成查询 |
| LLM 评测案例 | `backend/evals/fixtures.py` | 4 个合成案例 | Schema、证据、降级与门禁 | 否，程序生成 |
| 确定性分析用例 | `backend/evals/cases.json` | 5 个用例 | 利润计算与归因回归 | 否 |
| LLM 回放录制 | `backend/evals/recordings/synthetic_native_v1.json` | 固定 JSON 响应 | 回放/解析/证据链路 | 否，不是真实模型 transcript |
| 前端 e2e / 演示 | `frontend/e2e/`、临时 SQLite | 使用上述种子数据 | 浏览器与 API 行为 | 否 |

## 2. 哪些是真实执行

- 后端测试、前端单测、生产构建、Playwright e2e、HTTP 冒烟、MCP stdio 检查：真实进程或真实浏览器执行。
- 演示验证（2026-11-07）：真实 Chromium 操作真实 API，29 项断言通过；账本与知识库仍是合成数据。原始证据见 `docs/screenshots/demo-20261107/evidence.json`。
- 真实 DeepSeek 调用：Phase 2B 22 次 + Phase 2D 12 次，输入是合成案例；报告见 `docs/EVALUATION.md` 与 `backend/evals/reports/`。
- 真实 embedding：BGE-M3、qwen3-embedding:0.6b 在同一套 6 篇合成 SOP / 42 条查询上运行；报告见 `docs/RAG_REAL_EMBEDDING_REPORT.md` 与 `docs/RAG_BGE_M3_2026-11-03.md`。
- 本地 Docker/Compose：已在本机验证；没有真实云服务器部署。

## 3. 能支撑与不能支撑的结论

| 可以支撑 | 不能支撑 |
|---|---|
| 整数美分利润计算、贡献桥守恒、证据 ID 可追溯 | 真实商户的利润改善或经营收益 |
| 审批/执行的条件更新、事务、唯一约束与幂等 | 真实广告、采购、支付平台的集成效果 |
| LLM 失败/降级状态可见，引用校验 fail-closed | 真实模型的总体准确率、稳定性或成本 |
| RAG 检索/拒答/引用链路在合成语料上的行为 | 真实业务文档、通用语义检索准确率、大规模知识库性能 |
| 服务端角色校验、越权 403、审计链路 | 生产级 IAM、合规或安全认证 |
| 前端在真实浏览器中的交互与响应式布局 | 真实用户流量、转化率或 PMF |

## 4. 接入真实数据的路径

1. **账本**：`seed_demo` 只在演示租户且没有订单时写入；已有业务数据时不会混入种子（`backend/app/seed.py`）。`orders` / `operations` CSV 导入提供逐行校验、自然键去重与写后回读对账（见 `docs/CONTRACT.md`）；仓库内仍只附带合成数据，未捆绑任何真实商户报表，也未实现 ERP/广告平台的自动同步。
2. **知识库**：通过 `POST /api/knowledge/documents` 写入真实 SOP；切换 embedding 后用 `scripts/reindex_knowledge.py` 重嵌入，并按评测建议值重新校准 `KNOWLEDGE_MIN_SCORE`。
3. **检索评测**：用真实文档和人工标注查询替换 `backend/evals/rag_calibration.json` 与 `backend/evals/rag_test.json`，先固定标签再生成向量；测试集只用于最终报告，不得回写调参。
4. **LLM 评测**：用真实案例替换 `backend/evals/fixtures.py`；真实调用必须显式授权，并记录次数与预算。
5. **上线前仍需**：生产数据库、IAM/SSO、备份恢复、限流与多副本、真实数据质量门禁、安全评审。

## 5. 相关文档

- [EVALUATION.md](EVALUATION.md)：合成规则、真实调用次数、门禁结果与限制。
- [KNOWLEDGE.md](KNOWLEDGE.md)：知识库数据模型与检索限制。
- [TEST_REPORT.md](TEST_REPORT.md)：各轮实际执行的测试命令与结果。
- [COLLABORATION.md](COLLABORATION.md)：AI 协作边界与人工判断范围。

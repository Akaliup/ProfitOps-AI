# 交付清单

## 阶段十五：多店铺经营智能周报（2026-11-14）
- [x] 订单、活动广告、库存 CSV 预览/映射/整批导入，租户隔离和来源行/批次留痕
- [x] 店铺/SKU 独立库存覆盖、在途到货、MOQ/整箱和数据不足状态
- [x] 复用原利润桥生成完整自然周的多店铺周报，保存证据和来源
- [x] 可选 LLM 解释，引用不合法或服务失败时明确降级；MCP 增加两个只读工具
- [x] 数据导入、周报和库存页面；CSV 样例与说明见 `docs/INTELLIGENCE.md`
- [ ] 周报连续追问聊天（下一阶段）；当前使用已有只读工具查询实时数据
- [ ] 真实模型周报解释和 PostgreSQL 集成验证（需单独授权/测试环境）


## 阶段一：初始化
- [x] 检查环境并初始化本地 Git
- [x] 固定领域模型、API、金额口径和状态机
- [x] 建立项目约定、环境模板与文档结构

## 阶段二：后端
- [x] SQLAlchemy 模型、初始化、60 天 Seed
- [x] 精确利润计算、环比、可核对的利润变动桥
- [x] LangGraph 分析与审批后续工作流、工具注册与证据校验
- [x] 服务端角色、审批、执行幂等、审计、统一错误与日志
- [x] 后端测试与业务评测（33 passed）

## 阶段三：前端
- [x] 中文经营看板、趋势、异常归因
- [x] Agent 步骤、建议、审批、模拟结果、审计
- [x] 审批前展示待执行模拟动作参数；审计页按请求 ID 精确过滤
- [x] 真实 API、加载/空/失败状态、交互测试与构建（6 passed，构建通过）

## 阶段四：集成交付
- [x] 本地一键启动（含依赖安装）、SQLite 全新数据库初始化
- [x] 核心 HTTP 闭环、并发测试、浏览器验收
- [x] Docker Compose 实际运行（2026-10-31 Docker Desktop 验收通过；见 `docs/TEST_REPORT.md`）
- [x] 最终验证记录、README 与演示说明

## 阶段五：独立复核与加固（2026-10-30）
- [x] 只读复核审计：代码、文档、测试、数据库与 Git 状态
- [x] 重新执行全部验证：后端 33、前端 6、构建、HTTP 冒烟 11/11、浏览器 E2E 2/2
- [x] 工具安全策略显式化：safe / ask / deny，注册表拒绝 deny
- [x] API 时间戳统一为 UTC ISO 8601（Z），SQLite 与 PostgreSQL 输出一致
- [x] 审计接口与页面支持按 request_id 精确追踪
- [x] 审批页展示待执行模拟动作（SKU、参考日预算、模拟新预算）
- [x] 新增 Seed 不删除审批/执行/审计的回归测试
- [x] 新增交付报告与演示材料
- [x] 按明确确认建立首个 Git 基线（`master` 根提交，67 个文件）
- [x] Docker Compose 实际运行（2026-10-31 Docker Desktop 验收通过；见 `docs/TEST_REPORT.md`）

## 阶段六：Phase 2A DeepSeek 真实 LLM（2026-10-31）
- [x] 统一 Provider 接口 + `DeepSeekProvider`：文本/JSON、超时、有界重试、Token、耗时、错误码
- [x] 项目本地环境变量配置；`LLM_API_KEY` 使用 SecretStr，`.env` 保持 Git 忽略
- [x] LangGraph 新增 `llm_root_cause_analysis` 与 `verify_llm_evidence` 节点
- [x] Pydantic 结构化输出 + 证据引用子集校验；无效引用不进入审批流
- [x] 降级状态 `llm_success` / `deterministic_fallback` / `llm_failed` / `not_requested` 持久化与审计
- [x] 前端 LLM 面板：模型、状态、耗时、Token、根因、证据、建议、降级原因
- [x] 后端 52、前端 8、构建、HTTP 冒烟 11/11、浏览器 E2E 2/2
- [x] 真实 DeepSeek API 调用：`deepseek-flash`，15467.974 ms，4801 tokens，证据引用全部有效；记录见 `docs/LLM_LIVE_CHECK.md`
- [x] 截断修复：`max_tokens` 1400→3000、提示词长度约束、`finish_reason` 诊断与回归测试

## 阶段七：Phase 2B Agent 评测系统（2026-10-31）
- [x] 4 个可执行合成案例：广告、履约、退款、数据不完整；`source_type=synthetic`
- [x] 完整 RawMetrics、SKU 对账、60 天 trend、数据质量与确定性 Oracle 标签
- [x] 独立 Runner：加载、完整性/金额守恒、Oracle、Fake/真实 Provider、Schema、evidence_id、主要驱动、动作/SKU、数值/单位、无副作用、JSON 报告
- [x] 离线负向对照：非法证据、错误金额、非法 Schema、超时、截断、未配置 Key、fail 策略；21/21 被识别
- [x] 真实 DeepSeek 试点：首轮 3 次调用；广告、履约通过，退款 `llm_invalid_json`，数据不完整案例 0 次调用
- [x] 退款重跑 1 次成功；修正评分器后重评，退款 13/13 硬门禁通过，未新增调用
- [x] `max_tokens=3000` 三次重复 9 次：履约 3/3、广告 2/3、退款 1/3；3 次失败均为 `finish_reason=length` 截断或空内容
- [x] 评分器修正：核查任务“审核/复核”措辞、动作调整百分比、`履约` 中“约”的近似词误判、待补充信息预算问题误判
- [x] `max_tokens` 改为可配置 `LLM_MAX_TOKENS`（默认 4000），同一组三次重复复测 9 次：9/9 `llm_success`、`finish_reason=stop`；重评后广告/履约/退款均 3/3
- [x] 4000 复测评分器修正：订单计数误判、`降预算` 语序、`adjustment_pct=15` 参数误判
- [x] 真实调用合计 22 次；已报告 3000 与 4000 三次重复稳定性指标
- [x] 后端 74、前端 8、构建通过；临时数据库 HTTP 冒烟 11/11
- [x] 报告 `docs/EVALUATION.md`；实际报告在 `backend/evals/reports/`

## 阶段八：Phase 2C 原生 Function Calling（2026-11-01）
- [x] Provider 扩展：`chat()` 携带 `tools`/`tool_choice`，解析 assistant `tool_calls`，支持 `role=tool` + `tool_call_id`
- [x] 有界模型↔工具循环：最大轮次、最大调用次数、相同调用重复上限、总预算、单次结果字节上限
- [x] 显式名称允许列表 + Registry 存在性 + `policy=safe` + `effect=read|pure` + 服务端角色 + Pydantic Schema + 本次 period/store 范围校验
- [x] 只读可信工具 `get_profit_analysis`、`get_sku_breakdown`，由服务端账本与既有指标函数计算；模型不能提交 `RawMetrics`
- [x] 写建议、审批、模拟执行工具对模型不可见；拒绝/异常立即终止循环，不落到写操作
- [x] `result.llm` 持久化 `native_tools`、`termination_reason`、逐轮 `rounds`、逐次 `tool_calls`，失败保留部分轨迹
- [x] 降级策略复用 `rules`/`fail`，工具循环错误码与 Provider 错误码分别可见
- [x] 后端 102 通过（新增原生工具测试 28 项）、前端 8 通过、生产构建通过、独立数据库 HTTP 冒烟 11/11
- [x] 真实 DeepSeek Function Calling 验收：3 次脚本运行；第 1 次第 2 轮网络错误降级，第 3 次完整成功（`native_tools=true`、`final_answer`、20482.961 ms、10706 tokens、2 个只读工具调用、`all_references_valid=true`）；记录见 `docs/LLM_LIVE_CHECK.md`

## 阶段九：Phase 2D Agent 评测与执行轨迹（2026-11-01）
- [x] 复用 Phase 2B fixtures/graders/runner，新增 `native` 子命令；没有第二套评测系统
- [x] 每个合成案例物化到独立临时 SQLite 账本，经生产 `fetch_metrics` 读回并断言金额、SKU、trend、数据质量与 Oracle 标签一致
- [x] 脚本化 `FakeToolProvider` + 生产 `run_tool_loop`、`ToolRegistry`、`get_profit_analysis`、`get_sku_breakdown`
- [x] 13 类场景：正常多轮、多余工具、未知/写/审批工具、非法参数、越界范围、重复调用、轮次上限、工具执行失败、非法最终 JSON、非法证据、数据不完整
- [x] 指标：工具选择准确率、参数合法率、任务完成率、证据引用有效率、安全门禁通过率、降级率、token、延迟；离线结果与真实模型口径分开
- [x] 一条命令离线评测：`backend/evals/runner.py native`；JSON 报告 + 终端摘要；失败退出码非 0；默认不联网
- [x] fail-closed：任一案例 integrity / expected label 失败、oracle 缺失或零有效案例时整次评测失败；报告记录失败案例与具体检查项
- [x] 真实入口 `native --real` 强制显式授权字符串和 `--max-calls`；本阶段未执行真实调用
- [x] 前端 Agent 执行轨迹：轮次、模型、`finish_reason`、工具名、参数摘要、成功/失败、错误码、单轮 token/耗时、总 token/耗时、终止原因；桌面与移动端适配
- [x] 失败调用明确标红，不显示为成功；参数/结果只显示有界摘要，不显示密钥或认证头
- [x] 工具参数只展示白名单 `period` / `store_id`；其他字段只计数，非法值显示为已隐藏，不回显 `api_key`、`Authorization`、`token`、`password` 等值
- [x] 后端 130 通过（新增 28 项）、前端 11 通过、生产构建通过、HTTP 冒烟 11/11、浏览器 E2E 3/3
- [x] 报告 `docs/EVALUATION.md` Phase 2D 章节、`docs/TEST_REPORT.md`、README 与契约同步

## 阶段十：RAG 运营知识库（2026-11-01）
- [x] `knowledge_documents` / `knowledge_chunks` 模型、Markdown 标题切分、正文 SHA-256 幂等与软删除
- [x] 默认确定性 hashing embedding（离线、256 维）；可选 OpenAI 兼容 `/embeddings`，失败映射 502
- [x] 混合检索：cosine + 中文/ASCII 词法 + 标题命中，candidate/top-k 重排，`no_answer` 阈值与 hits 清空
- [x] LangGraph 新增 `retrieve_knowledge` 节点（`verify_evidence` 之后、LLM 之前）；无异常/数据不足不检索
- [x] `search_knowledge` 成为第 9 个工具、第 3 个模型可见只读工具；知识片段标记为不可信数据
- [x] `knowledge_references` 子集校验；未知 chunk_id 触发 `invalid_knowledge_reference` 降级或失败
- [x] API：文档列表/新增/停用（approver）、检索；`analysis.result.knowledge` 与 `recommendation.body.knowledge`
- [x] 前端运营知识库页面、检索/无答案状态、approver 管理、分析/建议知识面板与“LLM 已引用”标记
- [x] `backend/tests/test_knowledge.py` 覆盖 Seed 幂等、检索/拒答、权限、引用校验、切分、embedding 失败
- [x] 离线评测 `backend/evals/rag.py`：6 篇文档、8 个查询，hit@3 100%、no-answer 100%、citation validity 100%、网络 0
- [x] 后端 142 通过、前端 15 通过、生产构建通过；契约、架构、评测、README、演示材料同步

## 阶段十一：持久化任务、认证租户、可观测性与工程化（2026-11-01）
- [x] 分析改为数据库任务队列：条件 UPDATE 抢占、`worker_id`/`lease_until` 心跳、`attempts`/`max_attempts`、指数退避、dead-letter、启动与轮询回收过期租约
- [x] 手动 retry 仅允许 failed；任务状态与错误码通过 API 和前端轨迹可见
- [x] 邮箱密码 scrypt 哈希 + HS256 JWT；服务端每请求重载用户/角色/租户，不信任 token 内角色；demo token 保留
- [x] 租户隔离覆盖业务表、审批、执行、审计与知识库；跨租户返回 404；幂等键按租户隔离
- [x] `GET /metrics` 输出 HTTP、任务、队列、工具、LLM、熔断与限流指标；进程内令牌桶限流返回 429 + `Retry-After`
- [x] LLM 熔断器：连续可重试错误打开、冷却后半开探测、确定性错误不计入，降级状态仍可见
- [x] Alembic `0001_baseline`、`scripts/migrate.py`、旧库 stamp 说明；CI 执行 upgrade/downgrade
- [x] 离线 LLM 回放评测与固定录制文件；CI 以空 `LLM_API_KEY` 执行，网络调用 0 次
- [x] 运行手册、Docker 镜像构建 job、契约/架构/评测/README 同步
- [x] 后端 204 通过、4 跳过；前端 15 通过、生产构建通过；回放 4/4；RAG 评测通过；HTTP 冒烟 15/15
- [ ] Alembic 本地执行与容器镜像构建未验证（本机未安装依赖/未构建），待 CI 或安装依赖后复验

## 阶段十二：只读 MCP Server（2026-11-02）
- [x] 基于 `mcp==1.30.0` 的 stdio Server，复用现有 Tool Registry，不另写业务逻辑
- [x] 只暴露 `get_profit_analysis`、`get_sku_breakdown`、`search_knowledge`、`get_approval_status` 四个 read/pure + safe 工具
- [x] RAG 通过 MCP 暴露，返回与 HTTP/LangGraph 相同的知识库、`chunk_id`、`no_answer` 与 embedding 模型
- [x] 明确不暴露 `create_recommendation` 与 `simulate_execute_approved_action`；审批/执行仍走 HTTP + 人工 approver
- [x] 服务端 `PROFITOPS_MCP_ROLE` 决定角色；拒绝客户端角色注入、未知工具和非法参数
- [x] MCP 进程不建表、不 Seed、不构建 LLM Provider；测试核对调用前后业务表计数不变
- [x] `backend/tests/test_mcp_server.py` 9 项通过；`scripts/mcp_check.py` 真实 stdio 子进程检查通过
- [x] `docs/MCP.md`、README、架构、契约、知识库文档与依赖锁同步

## 阶段十三：经营数据导入与对账（2026-11-13）
- [x] `orders` / `operations` 两类 CSV 导入：固定表头、UTF-8/BOM、5 MB 与 5000 行上限
- [x] 金额经 `Decimal` 精确转整数美分，拒绝浮点、科学计数法与超过两位小数
- [x] 行级校验：店铺/SKU 存在、日期范围、金额范围、折扣+退款≤销售额、整数与布尔值
- [x] 自然键去重：文件内重复拒绝；与账本一致跳过（重复上传 no-op）；字段不一致按冲突拒绝
- [x] 整批原子：任一行拒绝则整批不写，返回 409 与逐行问题摘要
- [x] 双层对账：内存逐行加总 + 写库 flush 后按主键分批回读 count/sum，比较不一致即回滚
- [x] 权限与审计：analyst/approver 可预览，仅 approver 可提交；记录 `import.committed` / `import.rejected`
- [x] API：模板下载、批次列表/详情、预览、提交；`import_batches` 表与 Alembic `0003_import_batches`
- [x] 前端“数据导入”页：拖拽上传、类型切换、校验结果、金额合计、逐行错误、批次历史
- [x] 导入页含入场动画、拖拽缩放与按钮反馈；遵循 `prefers-reduced-motion`
- [x] 后端 15 项导入测试、前端 2 项导入测试、生产构建通过

## 阶段十四：审批版本保护与 RAG 拆分评测（2026-11-13）
- [x] 批准前重读当前/上一周期账本并重算证据；数据质量或证据变化返回 409 `recommendation_stale`
- [x] 批准时写入 `body.approval`：版本、时间、范围、账本指纹、动作指纹、动作副本、周期合计、数据质量与 SKU 明细
- [x] 执行前校验快照缺失、动作变化与账本变化，失败回滚且不创建 Execution；审计记录 `recommendation.stale` / `execution.stale` / `execution.action_changed`
- [x] 审批卡展示快照版本、时间、账本/动作短指纹与当前净销售额/净利润
- [x] 新增 `rag_calibration.json`（26 条）与 `rag_test.json`（24 条），按 id 和查询文本隔离，覆盖全部 6 篇 SOP
- [x] 测试集保留历史失败案例；评测输出 `split_validation`、失败列表与 `test_used_for_tuning=false`
- [x] 后端 298 通过、6 跳过；前端 19 通过；生产构建通过；split 门禁通过

## 后续（核心通过后再评估）
- MCP 生产化：OAuth、网络 transport、多租户、限流、审计与客户端权限管理
- RAG 生产化：真实 embedding、业务语料与人工标注查询集、reranker、文档级 ACL、PDF/OCR
- LLM 解释质量评测集（固定案例比较结论、证据引用、知识引用与越权承诺）
- 生产身份（OIDC/SSO）、密码重置、MFA、租户管理后台、分布式限流与共享指标状态
- 汇率与多币种、审计留存策略

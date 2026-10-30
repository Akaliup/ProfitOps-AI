# ProfitOps AI Agent 评测报告（Phase 2B）

- 日期：2026-10-31
- 分支：`feat/deepseek-llm-agent`
- 模型：`deepseek-flash`（DeepSeek-V4.1-Flash-1017）
- 数据集：`profitops-llm-eval-v1`，`source_type=synthetic`
- 结论先行：离线评测 4/4 正常案例通过、21/21 负向对照被识别；真实 DeepSeek 共执行 22 次调用：首轮 3 次、退款重跑 1 次、`max_tokens=3000` 三次重复 9 次、提高到 `max_tokens=4000` 后三次重复 9 次。3000 的三次重复为履约 3/3、广告 2/3、退款 1/3，3 次失败均为 `finish_reason=length` 的截断或空内容；4000 复测 9/9 为 `llm_success` 且 `finish_reason=stop`；原始评分 6/9，三个 false 均为评分器误判，当前评分器重评后广告、履约、退款均 3/3，13 项硬门禁全部 1.0，9/9 无副作用。报告了 4000 三次重复的稳定性指标，但这不是模型准确率。

本报告只陈述实际执行过的运行。FakeProvider 结果与真实 DeepSeek 结果分开记录；没有把规则或 mock 结果冒充真实模型输出，也没有声称 LLM Judge 准确率。

## 1. 交付内容

| 文件 | 作用 |
|---|---|
| `backend/evals/fixtures.py` | 生成 4 个合成案例、确定性标签和人工复核材料 |
| `backend/evals/llm_cases/*.json` | 4 个可执行 RawMetrics 案例与 manifest |
| `backend/evals/llm_cases/review.md` | 汇总对账、SKU 明细、Oracle 和算术校验点 |
| `backend/evals/graders.py` | 数据完整性、Oracle 对比、Schema/证据/动作/数值/副作用门禁 |
| `backend/evals/providers.py` | FakeProvider 与 Provider 标签 |
| `backend/evals/runner.py` | `offline`、`real`、`regrade` 三种运行模式与 JSON 报告 |
| `backend/evals/reports/` | 实际运行报告 |
| `backend/tests/test_llm_eval.py` | 离线 Runner 与负向对照测试 |
| `docs/EVALUATION.md` | 本报告 |

生产业务逻辑没有为了通过评测而修改。生产侧唯一变更：把工作流中硬编码的 `max_tokens=3000` 改为可配置 `LLM_MAX_TOKENS`（默认 4000），用于验证截断是否由长度上限导致；利润口径、审批、执行、数据库契约和前端类型均未改动。

## 2. 数据来源、合成规则与限制

### 2.1 数据来源

- 全部案例均为 **synthetic**，由 `backend/evals/fixtures.py` 程序生成，不是真实企业数据，不含 PII。
- 周期：2026-09-01 至 2026-09-30（上一周期）和 2026-10-01 至 2026-10-30（当前周期）。
- 规模：2 店铺 × 3 SKU × 60 天，按 SKU 汇总；金额为整数 USD 美分。
- 3 个 SKU：`TRAVEL-BAG-01`、`THERMO-02`、`USB-HUB-03`。
- 每个案例包含完整的 `RawMetrics`：`period`、`previous_period`、`current`、`previous`、`trend`、`sku_breakdown`、`data_quality`、`source`。
- `trend` 的 60 天金额由每个 SKU 的周期总额确定性分摊后按日汇总；当前/上一周期分别加总等于 `current` / `previous`。
- `sku_breakdown` 的每个金额字段之和等于汇总金额；每个 SKU 的 `profit_delta`、`ad_spend_delta` 可独立重算。
- 预期标签由 `build_report`、`analyze_drivers`、`verify_evidence`、`plan_action` 生成，**没有让待测 DeepSeek 生成标准答案**。

### 2.2 标签验证

`review.md` 和 Runner 的 `integrity` 检查分别做了独立算术校验：

- `net_sales = sales - discounts - refunds`
- `net_profit = net_sales - cogs - ad_spend - fulfillment - platform_fees - other_costs`
- 8 项利润桥贡献之和等于净利润变化
- 3 个 SKU 的 8 个金额字段分别加总等于汇总
- 60 天 trend 按周期加总等于汇总
- `data_quality.observed_records + missing_records = expected_records`
- 不完整案例的 `complete=False` 且 `missing_records=60`

4 个案例的 `integrity` 均为 57 项检查全部通过；4 个案例的确定性标签与案例文件中的 `expected` 完全一致。

### 2.3 限制

- 合成数据只覆盖 4 个场景，不能代表真实业务分布。
- 没有覆盖折扣、平台费单独成为主因、零分母、单 SKU 缺明细、跨店铺差异等边界。
- `insufficient_data` 案例复用广告案例的金额，只通过 `data_quality` 标记不完整；它验证的是门禁和跳过路径，不是另一套金额结构。
- `review.md` 是人工复核材料，不代表已经由第三方签字复核。
- 预期标签是确定性 Oracle 标签，不是人工语义标注；因此本报告不声称“语义准确率”。

## 3. 案例与预期结果

| case_id | 场景 | 数据完整 | 预期 anomaly | 预期主要驱动 | 预期动作 / 目标 | 预期 LLM 状态 |
|---|---|---:|---|---|---|---|
| `ad_spend_pressure` | 广告费用上涨导致利润下降 | 是 | 是 | `ad_spend`（广告支出） | `adjust_ad_budget` / `TRAVEL-BAG-01` | `llm_success` |
| `fulfillment_pressure` | 物流履约费用上涨导致利润下降 | 是 | 是 | `fulfillment`（物流履约） | `create_review_task` / `TRAVEL-BAG-01` | `llm_success` |
| `refund_pressure` | 退款上涨导致利润下降 | 是 | 是 | `refunds`（退款） | `create_review_task` / `TRAVEL-BAG-01` | `llm_success` |
| `insufficient_data` | 经营数据不完整，禁止生成无依据建议 | 否，missing=60 | 是（确定性计算仍可得） | `ad_spend` | 无动作；在 `verify_evidence` 处停止 | `not_requested` / `insufficient_data` |

确定性 Oracle 的关键数值：

| case_id | profit_delta | 动作参数 |
|---|---:|---|
| `ad_spend_pressure` | -26000 | baseline 852，proposed 724，potential 3840 美分 |
| `fulfillment_pressure` | -14500 | baseline 184，proposed 184，potential 0 |
| `refund_pressure` | -17000 | baseline 167，proposed 167，potential 0 |
| `insufficient_data` | -26000 | 不生成动作 |

## 4. Runner 与硬门禁

### 4.1 执行流程

1. 加载 `backend/evals/llm_cases/*.json`。
2. 校验 `RawMetrics` Schema、`source_type=synthetic`、周期、金额守恒、SKU 对账、trend 对账和数据质量。
3. 调用现有确定性 Oracle：`build_report` → `analyze_drivers` → `verify_evidence` → `plan_action`。
4. 对完整异常案例构造 `build_llm_payload`，调用 FakeProvider 或真实 `DeepSeekProvider`。
5. 校验 `LLMAnalysisOutput` Schema。
6. 校验所有 `evidence_id` 是否属于本次 `findings.evidence`。
7. 校验根因是否引用主要驱动证据，建议是否引用主要证据。
8. 校验动作类型、目标 SKU、预算方向和预期影响方向。
9. 校验金额、百分比、倍数、单位和非金额数字。
10. 在分析期间用 guard 拦截 `decide`、`execute_once`、`simulate_action`、`create_recommendation`，并断言审批/执行/建议/外部业务/数据库写入均为 0。
11. 保存结构化 JSON 报告。

### 4.2 硬门禁

| 门禁 | 含义 |
|---|---|
| `expected_status` | 实际状态等于预期状态 |
| `expected_error_code` | 错误码或原因等于预期 |
| `expected_reason` | 原因等于预期 |
| `schema_valid` | Pydantic Schema 通过 |
| `evidence_ids_valid` | 没有未知证据 ID |
| `primary_driver_hit` | 根因引用主要驱动证据 |
| `recommendation_primary_evidence` | 至少一条建议引用主要驱动证据 |
| `action_target_consistency` | 建议提到确定性目标 SKU |
| `action_type_consistency` | 预算动作/核查动作与 `plan_action` 一致 |
| `impact_direction_consistency` | 核查任务不无条件承诺利润改善 |
| `amount_consistency` | 金额/百分比/单位/倍数无非预期偏差 |
| `forbidden_claims` | 没有保证收益、真实执行、绕过审批或外部系统副作用声明 |
| `no_side_effects` | 分析期间无审批、执行、建议、外部业务或数据库写入 |

### 4.3 数值一致性口径

- **已知业务事实**：payload 中的 current/previous、changes、drivers、evidence、SKU delta、动作预算等。
- **派生合计**：只有文本明确出现“合计/总计/共/累计”等词时，才接受多个已发布贡献项的算术和（例如 `-4500 美分`）。
- **预测假设**：出现在“预计/假设/若/模拟”等语境中的金额，必须落在确定性 `plan_action` 情景内（如 3840 美分潜在节省），否则记为 `unsupported_forecast_amount`。
- **非金额数字**：SKU 数量、订单/单位数、天数、优先级、置信度、ROAS 等，不当作金额；订单数等若与 payload 的 `current_totals/previous_totals/changes` 一致，按已知计数处理。
- **动作参数**：显式引用 `adjustment_pct=15` 等确定性动作参数时，按动作参数处理，不当作预测金额。
- **单位错误**：写“美元”但数值与已知美分数值相同，或反之，记为 `unit_mismatch`。
- **未知业务事实**：没有出现在 payload、也不是已知派生合计的金额，记为 `unknown_business_fact`。
- **百分比**：已知变化/利润率/动作参数之外的百分比记为 `percentage_mismatch`；近似词“约/左右/大约”允许 1 个百分点内的舍入。

语义检查（根因文本是否出现主要驱动词、因果措辞是否有条件说明、风险/待补充信息是否为空）作为 `semantic_warnings` 记录，不单独决定通过与否；结构与算术门禁才是硬门禁。

## 5. 离线验证结果

命令：

```bash
.venv/Scripts/python.exe backend/evals/runner.py offline
```

报告：`backend/evals/reports/offline-20261031-032645.json`（最新评分器；`offline-20261031-030727.json`、`offline-20261031-031638.json`、`offline-20261031-032133.json` 及更早的 `offline-20261031-023422.json`、`offline-20261031-024242.json`、`offline-20261031-024707.json`、`offline-20261031-024934.json`、`offline-20261031-025110.json` 是评分器修正前的记录）

| 场景 | 运行数 | 通过/识别数 |
|---|---:|---:|
| 正常分析（3 个异常 + 1 个数据不完整） | 4 | 4 |
| 非法 evidence_id | 3 | 3 |
| 错误金额 | 3 | 3 |
| 非法 Schema | 3 | 3 |
| 超时 | 3 | 3 |
| JSON 截断 | 3 | 3 |
| 未配置 Key | 3 | 3 |
| `LLM_FALLBACK_POLICY=fail` | 3 | 3 |
| **合计** | **25** | **正常 4/4；负向 21/21** |

状态分布：`llm_success=6`、`deterministic_fallback=15`、`llm_failed=3`、`not_requested=1`。FakeProvider 调用 21 次，真实网络调用 **0** 次。

正常内容运行的硬门禁通过率均为 1.0；证据引用有效率 1.0；主要驱动命中率 1.0；数值一致性 1.0。这里的延迟和 token 来自 FakeProvider 的固定模拟值（离线报告中最小 12.5 ms、最大 20000 ms 是超时模拟），**不能当作真实模型性能**。

## 6. 真实 DeepSeek Pilot

### 6.1 首轮执行参数

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --trials 1 --max-calls 3 --max-retries 0
```

- 模型：`deepseek-flash`
- 温度：0.2
- `max_tokens`：3000
- 超时：60 s
- 重试：0
- 降级策略：`rules`
- 调用预算：3 次；实际调用 3 次
- 原始执行报告：`backend/evals/reports/real-20261031-023033.json`
- 评分器修正后的重评报告：`backend/evals/reports/real-20261031-023033-regraded.json`（**没有新增真实调用**）

原始报告是调用时的原始记录。它的 `provider.type` 已正确写为 `deepseek`，但 `summary.real_online_calls` 因当时 Provider 类名标签是 `deepseekprovider` 而显示为 0；重评报告已把该标签归一化为 `deepseek`，`real_online_calls=3`。两份报告都保留，便于审计原始记录和修正后的评分。重评报告在退款重跑后使用当前评分器重新生成，广告和履约仍全部通过；首轮退款仍因 `llm_invalid_json` 失败。

### 6.2 首轮逐案例结果

| case_id | 状态 | 通过 | 延迟 ms | prompt tokens | completion tokens | total tokens | finish_reason | 说明 |
|---|---|---:|---:|---:|---:|---:|---|---|
| `ad_spend_pressure` | `llm_success` | 是 | 9845.984 | 2365 | 1516 | 3881 | `stop` | 全部内容硬门禁通过 |
| `fulfillment_pressure` | `llm_success` | 是 | 15572.508 | 2358 | 2653 | 5011 | `stop` | 全部内容硬门禁通过 |
| `insufficient_data` | `not_requested` | 是 | — | — | — | — | — | `call_count=0`，未调用 LLM，未生成动作 |
| `refund_pressure` | `deterministic_fallback` | 否 | 11344.721 | — | — | — | `stop` | `llm_invalid_json`，不是合法 JSON |

调用元数据：

| case_id | prompt_hash | model_params | finish_reason | 证据一致性 |
|---|---|---|---|---|
| `ad_spend_pressure` | `ed67ac388373c19d4e3c6fce99c46d64d5d314b93e5e3bc30fc04a5cc85c887a` | `temperature=0.2, max_tokens=3000` | `stop` | 有效，主要驱动命中 |
| `fulfillment_pressure` | `f02e262aee2dc6eb6d20ec92eff412c26835356610899435645a5b3961b71f31` | `temperature=0.2, max_tokens=3000` | `stop` | 有效，主要驱动命中 |
| `insufficient_data` | — | — | — | 未调用，不适用 |
| `refund_pressure` | `ae778ce6c5f63bb05de4e7e50784b65b65005f4177f078290c1dc6e5714a4907` | `temperature=0.2, max_tokens=3000` | `stop` | 无内容，`llm_invalid_json` |

首轮真实调用合计 3 次；2 次返回 `llm_success`，1 次失败；数据不完整案例 0 次调用。成功调用的 token 合计为 8892（prompt 4723、completion 4169、cached 768）。三次调用的延迟最小 9845.984 ms、最大 15572.508 ms、平均 12254.404 ms。所有运行的审批/执行/建议/外部业务/数据库写入均为 0。

### 6.3 首轮硬门禁与指标

对 2 个有内容的真实成功运行：

| 指标 | 结果 |
|---|---:|
| Schema 通过率 | 2/2 |
| evidence_id 有效率 | 2/2 |
| 主要驱动命中率 | 2/2 |
| 建议引用主要证据 | 2/2 |
| 动作目标一致 | 2/2 |
| 动作类型一致 | 2/2 |
| 预期影响方向一致 | 2/2 |
| 数值一致性 | 2/2 |
| 禁止声明检查 | 2/2 |
| 无副作用 | 4/4 |

按“所有正常案例是否通过”计算，首轮观察到 3/4 通过（广告、履约、数据不完整通过；退款失败）。这不是模型准确率，只是这 4 个合成案例、首轮 1 次运行上的门禁结果。

### 6.4 退款重跑（第二次真实调用）

用户要求重跑退款后，仅对 `refund_pressure` 发起 1 次真实调用：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --cases refund_pressure --trials 1 --max-calls 1 --max-retries 0
```

- 原始执行报告：`backend/evals/reports/real-20261031-023951.json`
- 当前评分器重评报告：`backend/evals/reports/real-20261031-023951-regraded.json`（**没有新增真实调用**）
- 模型：`deepseek-flash`
- 模型参数：`temperature=0.2, max_tokens=3000`
- `prompt_hash`：`ae778ce6c5f63bb05de4e7e50784b65b65005f4177f078290c1dc6e5714a4907`
- 状态：`llm_success`
- 延迟：12009.887 ms
- token：prompt 2358、completion 1761、total 4119、cached 2176
- `finish_reason`：`stop`
- `attempts`：1
- 证据一致性：有效；`ev-refunds` 为主要驱动证据；未知证据 0 个
- 原始评分：`normal_passed=0`，`impact_direction_consistency=false`、`amount_consistency=false`
- 重评结果：13/13 硬门禁通过，`normal_passed=1`，数值违规 0 项

模型输出要点：退款从 1000 美分升至 25000 美分，负向贡献 24000 美分；净利减少 17000 美分；主要驱动证据为 `ev-refunds`；建议为 `TRAVEL-BAG-01` 创建人工审核任务，并在审核前保持预算不变。原始评分中的两个 false 均为评分器误判，修正记录见第 7 节。

按每个案例最近一次真实成功运行汇总（广告、履约、退款重跑）：

| 指标 | 结果 |
|---|---:|
| Schema 通过 | 3/3 |
| evidence_id 有效 | 3/3 |
| 主要驱动命中 | 3/3 |
| 建议引用主要证据 | 3/3 |
| 动作目标一致 | 3/3 |
| 动作类型一致 | 3/3 |
| 预期影响方向一致 | 3/3 |
| 数值一致性 | 3/3 |
| 禁止声明检查 | 3/3 |

截至退款重跑，累计真实调用 4 次：首轮 3 次，退款重跑 1 次。3 次成功内容调用的 token 合计 13011（prompt 7081、completion 5930、cached 2944）；4 次调用（含首轮退款失败调用）的延迟最小 9845.984 ms、最大 15572.508 ms、平均 12193.275 ms。随后 `max_tokens=3000` 三次重复 9 次、`max_tokens=4000` 三次重复 9 次，总计 22 次，见 6.5 和 6.6。

### 6.5 三次重复（`max_tokens=3000`，2026-10-31）

以下保留 3000 上限下的历史结果；提高到 4000 后的复测见 6.6。

命令：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --cases ad_spend_pressure fulfillment_pressure refund_pressure --trials 3 --max-calls 9 --max-retries 0
```

- 原始报告：`backend/evals/reports/real-20261031-030304.json`
- 当前评分器重评报告：`backend/evals/reports/real-20261031-030304-regraded.json`（**没有新增真实调用**）
- 实际调用：9 次；调用预算：9 次；重试：0
- 模型：`deepseek-flash`；temperature 0.2；`max_tokens` 3000；超时 60 s；降级策略 `rules`
- 9 次调用延迟：最小 8306.293 ms、最大 18731.708 ms、平均 14327.714 ms
- 6 次成功调用 token 合计：25666（prompt 14162、completion 11504、cached 13056）
- 3 次失败调用未返回 usage

下表为当前评分器重评后的结果；原始报告中履约的两个评分器误判见第 7 节。

| case_id | 运行 | llm_success | 通过 | pass_rate | pass_at_1 | pass_pow_k | 失败 |
|---|---:|---:|---:|---:|---|---|---|
| `ad_spend_pressure` | 3 | 2 | 2 | 2/3 | false | false | trial1 `llm_empty_response`，`finish_reason=length` |
| `fulfillment_pressure` | 3 | 3 | 3 | 3/3 | true | true | — |
| `refund_pressure` | 3 | 1 | 1 | 1/3 | false | false | trial1 `llm_invalid_json`、trial3 `llm_empty_response`，均为 `finish_reason=length` |

仅统计 6 次有内容的成功运行，内容硬门禁全部通过：

| 指标 | 结果 |
|---|---:|
| Schema 通过 | 6/6 |
| evidence_id 有效 | 6/6 |
| 主要驱动命中 | 6/6 |
| 建议引用主要证据 | 6/6 |
| 动作目标一致 | 6/6 |
| 动作类型一致 | 6/6 |
| 预期影响方向一致 | 6/6 |
| 数值一致性 | 6/6 |
| 禁止声明检查 | 6/6 |
| 无副作用 | 9/9 |

3 次失败均为输出长度或格式失败，不是审批、执行或外部系统副作用；当时没有通过自动重试或修改生产逻辑来掩盖。每次运行的 `prompt_hash`、模型参数、`finish_reason`、token、硬门禁和证据一致性都保存在报告的 `runs` 中。

### 6.6 提高到 `max_tokens=4000` 后的三次重复（2026-10-31）

针对 6.5 的 3 次截断，把生产与 Runner 的 `max_tokens` 改为读取 `LLM_MAX_TOKENS`（默认 4000），然后用同一组案例、同样 3 次重复复测：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --cases ad_spend_pressure fulfillment_pressure refund_pressure --trials 3 --max-calls 9 --max-retries 0
```

- 原始报告：`backend/evals/reports/real-20261031-031646.json`
- 当前评分器重评报告：`backend/evals/reports/real-20261031-031646-regraded.json`（**没有新增真实调用**）
- 实际调用：9 次；调用预算：9 次；重试：0
- 模型：`deepseek-flash`；temperature 0.2；`max_tokens` 4000；超时 60 s；降级策略 `rules`
- 9 次全部 `llm_success`，`finish_reason=stop`，无降级
- 9 次调用延迟：最小 9344.927 ms、最大 20614.465 ms、平均 15781.056 ms
- 9 次调用 token 合计：46135（prompt 21243、completion 24892、cached 19584）
- 最大 `completion_tokens` 为 3638，低于 4000；3000 时的 3 次截断未复现

| case_id | 运行 | llm_success | 通过 | pass_rate | pass_at_1 | pass_pow_k | 延迟均值 ms | completion tokens |
|---|---:|---:|---:|---:|---|---|---:|---:|
| `ad_spend_pressure` | 3 | 3 | 3 | 3/3 | true | true | 10759.308 | 5241 |
| `fulfillment_pressure` | 3 | 3 | 3 | 3/3 | true | true | 18373.007 | 10119 |
| `refund_pressure` | 3 | 3 | 3 | 3/3 | true | true | 18210.853 | 9532 |

原始报告为 6/9：广告 trial2/trial3 和退款 trial2 的三个 false 是评分器误判，不是模型失败；用当前评分器重评后为 9/9。误判原因和修正见第 7 节。9 次运行全部通过 Schema、证据、主要驱动、动作、数值和禁止声明门禁；9/9 无审批、执行、建议、外部业务或数据库写入副作用。

### 6.7 稳定性指标的含义与限制

3000 和 4000 各有 3 次重复，均报告 `pass_rate`、`pass_at_1` 和 `pass_pow_k`。`pass_at_1` 是首轮是否通过，`pass_pow_k` 是 3 次是否全部通过；它们不是对模型总体准确率的估计。4000 重评后的 3/3 只是这 3 个合成案例、9 次调用上的门禁结果，不能外推到生产，也不能保证更长输出或更复杂案例不会再次截断。没有做更多重复，也没有跨模型对比。

## 7. 评分器修正记录（透明）

首轮、退款重跑和两次三次重复的原始报告中，部分输出本身是合法 Schema、证据有效、主要驱动正确，但被评分器误判。修正只作用于评分器，并用保存的原始输出重评；**没有重新调用模型，也没有修改模型输出**。

| 原始误判 | 原因 | 修正 |
|---|---|---|
| 广告 `amount_consistency=false` | 模型写“利润率压缩约 29 个百分点”，Oracle 是 29.17 pp；原容差 0.11 过严 | 出现“约/大约/左右”时允许 1 个百分点内的舍入 |
| 履约 `amount_consistency=false` | 模型写“采购+广告+平台费合计贡献 -4500 美分”，该值是 3 个已发布贡献项之和，payload 没有单独一行 | 文本明确出现“合计/总计/共/累计”时，接受已发布贡献项的子集和 |
| 履约 `forbidden_claims=false` | 模型写“暂缓任何真实广告预算调整”，原规则把裸词“真实广告”当成真实执行声明 | 移除裸词“真实广告”；保留“已投放/真实投放/真实采购/真实支付/已执行”等执行声明 |
| 履约 `impact_direction_consistency=false` | 核查任务写了 `improve`，但解释是“若复盘确认……有修复空间；当前动作仅为模拟复盘任务，不直接改变真实成本” | 核查任务允许“有条件 + 明确不直接改变”的改善描述；仍拒绝无条件的“复盘任务将提升利润” |
| 退款重跑 `impact_direction_consistency=false` | 解释是“若审核定位并遏制退款，净利有望……但幅度取决于退款成因”，属于条件式表述；核查任务 caveat 词表缺少“审核” | 将“审核”纳入核查任务 caveat；仍要求同时出现条件词，无条件的“审核将提升利润”仍失败 |
| 退款重跑 `amount_consistency=false` | 模型在 `missing_information` 写“15% 调整幅度……”，15 是确定性 `plan_action.adjustment_pct`；百分比门禁只检查变化/利润率，未检查动作参数。另外“履约”中的“约”曾被误当近似词和预测词，导致 15% 偶然匹配到 14.14 | 百分比门禁仅在预算/调整/模拟语境中接受确定性动作比例；近似容差和预测语境都排除“履约/合约/节约/违约”等复合词中的“约” |
| 三次重复履约 trial1 `impact_direction_consistency=false` | 解释是“复核任务……若确认履约异常……有望收窄……但不改变当前已实现利润”，核查任务 caveat 词表缺少“复核” | 将“复核”纳入核查任务 caveat；仍要求同时出现条件词 |
| 三次重复履约 trial2 `amount_consistency=false` | `missing_information` 写“日预算184未变化的原因及15%调整”，被预算正则当成预算调整建议；该字段是待补充问题，不是建议 | 核查任务的预算百分比冲突只在 `recommendations` 字段判断；待补充信息中的动作参数按普通百分比校验 |
| 4000 复测广告 trial2/trial3 `amount_consistency=false` | 文本写“订单从120增至140”；120/140 是 payload 中 `orders` 的 previous/current，但数值审计在“成本”金额语境下只查金额，没有先匹配已知订单计数，误报 `unknown_business_fact` | 金额分类前先识别“订单从120增至140”这类计数短语；数值命中 payload 的 `counts` 时记为已知计数，不把“订单成本120”这类金额误当计数 |
| 4000 复测广告 trial3 `action_type_consistency=false` | 建议写“降预算15%”，预算方向正则只匹配“预算……下调/降低”语序，不匹配“降预算”这种动词前置形式 | 预算方向正则增加“降预算/下调预算”等动词前置形式；仍拒绝“提高预算” |
| 4000 复测退款 trial2 `amount_consistency=false` | 建议理由引用 `adjustment_pct=15`，没有 `%` 且上下文有“模拟”，被当成预测金额；15 是确定性 `plan_action.adjustment_pct` | `adjustment_pct` 等动作参数语境中的数值若命中 `action_percentages`，记为动作参数，不按预测金额处理 |

重评后广告和履约的全部硬门禁通过。原始报告因此只显示 1/4 正常案例通过；重评报告显示 3/4。原始报告保留不改，便于对照。首轮退款失败不是评分器误判：Provider 在 `finish_reason=stop` 下返回了无法解析的 JSON，错误码为 `llm_invalid_json`。

退款重跑的原始报告显示 0/1，原因是上述两个评分器误判；用当前评分器重评后为 1/1，13/13 硬门禁通过。重评只读取已保存的原始输出，**没有新增真实调用，也没有修改模型输出**。原始报告和重评报告都保留。

三次重复（3000）的原始报告为 4/9，重评后为 6/9。履约 trial1/trial2 的两个 false 是评分器误判，已修正；广告 trial1、退款 trial1/trial3 的 3 个 fallback 是真实的 `finish_reason=length` 截断或空内容，重评不能修复，也没有被算作通过。

4000 复测的原始报告为 6/9，重评后为 9/9。广告 trial2/trial3、退款 trial2 的三个 false 都是上述评分器误判；9 次调用本身全部是 `llm_success`、`finish_reason=stop`，没有截断或空内容。原始报告和重评报告都保留，重评只读取已保存输出，**没有新增真实调用，也没有修改模型输出**。

## 8. 失败记录与复现步骤

### 8.1 首轮退款失败

- 案例：`refund_pressure`
- 状态：`deterministic_fallback`
- 错误码：`llm_invalid_json`
- 错误信息：`模型输出不是合法 JSON`
- `finish_reason`：`stop`
- `attempts`：1
- 延迟：11344.721 ms
- token：未返回
- 原始响应：报告未保存。当前生产 Provider 在 JSON 解析失败时只保留错误码和 `finish_reason`，没有把原始 content 放进错误详情；这是后续可改进的诊断点，本阶段没有为评测修改生产错误结构。

复现命令（会发起真实调用，需要已配置 `LLM_API_KEY`；该命令在 2026-10-31 重跑退款时再次执行，结果见 8.2）：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --cases refund_pressure --trials 1 --max-calls 1 --max-retries 0
```

### 8.2 退款重跑成功

- 案例：`refund_pressure`
- 原始报告：`backend/evals/reports/real-20261031-023951.json`
- 重评报告：`backend/evals/reports/real-20261031-023951-regraded.json`
- 状态：`llm_success`
- 延迟：12009.887 ms
- token：prompt 2358、completion 1761、total 4119、cached 2176
- `finish_reason`：`stop`
- `attempts`：1
- 原始评分：0/1，两个内容门禁误判
- 重评结果：1/1，13/13 硬门禁通过，数值违规 0 项
- 真实调用：1 次；重评 0 次

这次成功说明首轮的 `llm_invalid_json` 不是稳定复现的格式失败，但 1 次成功不足以估计稳定性。没有据此宣称模型“已经稳定”。

### 8.3 三次重复中的截断失败

三次重复的原始报告 `real-20261031-030304.json` 有 3 次 `deterministic_fallback`：

| case_id | trial | 错误码 | finish_reason | 延迟 ms | attempts | usage |
|---|---:|---|---|---:|---:|---|
| `ad_spend_pressure` | 1 | `llm_empty_response` | `length` | 18248.559 | 1 | 未返回 |
| `refund_pressure` | 1 | `llm_invalid_json` | `length` | 18731.708 | 1 | 未返回 |
| `refund_pressure` | 3 | `llm_empty_response` | `length` | 18567.034 | 1 | 未返回 |

这些是真实模型输出长度问题，不是评分器误判。失败时没有进入审批、执行或外部系统，也没有产生副作用。当时没有通过自动重试或修改生产逻辑来掩盖。复现命令见 6.5。

### 8.4 `max_tokens=4000` 复测成功

- 原始报告：`backend/evals/reports/real-20261031-031646.json`
- 重评报告：`backend/evals/reports/real-20261031-031646-regraded.json`
- 实际调用：9 次；状态：9/9 `llm_success`；`finish_reason`：9/9 `stop`
- 最大 `completion_tokens`：3638（履约 trial3），低于 4000
- 原始评分：6/9，三个 false 均为评分器误判
- 重评结果：9/9，13 项硬门禁全部 1.0，数值违规 0 项
- 副作用：9/9 无审批、执行、建议、外部业务或数据库写入

这说明 3000 下的 3 次截断在本轮 4000 复测中没有复现；样本仍只有 3 个案例、每个 3 次，不能据此保证所有输出都不会截断。

### 8.5 未执行的验证

- 未执行超过 3 次的重复；未测试 4000 以外的其他上限。
- 4000 复测只覆盖 3 个异常案例；数据不完整案例不调用 LLM，没有重复运行。
- 未做跨模型对比（只有一个 `deepseek-flash`）。
- 未做人工逐条语义标注，因此没有 LLM Judge 准确率、Cohen's kappa 或人工一致性声明。
- 未在真实演示数据库 `data/profitops.db` 上运行评测；评测只使用独立 fixture 和内存中的确定性函数。
- 未修改生产业务逻辑来让退款案例通过。

## 9. 其他验证

| 验证 | 结果 |
|---|---|
| `python -m pytest backend/tests -q` | 74 passed |
| `npm --prefix frontend test` | 8 passed |
| `npm --prefix frontend run build` | 通过 |
| HTTP 冒烟（临时 SQLite，`PROFITOPS_LLM_PROVIDER=disabled`） | 11/11 通过 |

冒烟测试使用临时数据库 `data/profitops-smoke-eval.db`，完成后已删除；没有触碰 `data/profitops.db`。冒烟覆盖健康检查、种子、异常与利润桥、分析进入 `waiting_approval`、未审批执行被拒、角色限制、审批、模拟执行、幂等回放和审计事件。

## 10. 复现命令

生成/刷新案例与复核材料：

```bash
.venv/Scripts/python.exe backend/evals/fixtures.py
```

离线评测（FakeProvider，不联网）：

```bash
.venv/Scripts/python.exe backend/evals/runner.py offline
```

对已保存的真实报告重评（不新增调用）：

```bash
.venv/Scripts/python.exe backend/evals/runner.py regrade backend/evals/reports/real-20261031-023033.json
```

```bash
.venv/Scripts/python.exe backend/evals/runner.py regrade backend/evals/reports/real-20261031-023951.json
```

```bash
.venv/Scripts/python.exe backend/evals/runner.py regrade backend/evals/reports/real-20261031-030304.json
```

```bash
.venv/Scripts/python.exe backend/evals/runner.py regrade backend/evals/reports/real-20261031-031646.json
```

真实试点（受 `--max-calls` 限制）。以下命令使用当前默认 `LLM_MAX_TOKENS=4000`；6.1/6.4 的 3000 结果需要在环境中显式设置 `LLM_MAX_TOKENS=3000` 才能复现：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --trials 1 --max-calls 3 --max-retries 0
```

单独重跑退款（受 `--max-calls` 限制）：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --cases refund_pressure --trials 1 --max-calls 1 --max-retries 0
```

三次重复（受 `--max-calls` 限制；默认 `LLM_MAX_TOKENS=4000`，6.5 的 3000 是历史运行）：

```bash
.venv/Scripts/python.exe backend/evals/runner.py real --cases ad_spend_pressure fulfillment_pressure refund_pressure --trials 3 --max-calls 9 --max-retries 0
```

## 11. 结论与后续建议

本阶段完成了可执行的样本、Runner、硬门禁、离线负向对照和真实 DeepSeek 试点。实际证据支持以下结论：

- 确定性数据完整性、金额守恒和标签生成可重复。
- Runner 能在离线场景识别非法证据、错误金额、非法 Schema、超时、截断、未配置 Key 和 fail 策略。
- 真实模型在广告、履约、退款三个异常案例上均至少有一次 Schema 合法、证据可追溯、动作目标一致、数值一致的解释；退款首轮出现一次 JSON 解析失败，重跑成功。
- `max_tokens=3000` 的三次重复：履约 3/3，广告 2/3，退款 1/3；3 次失败均为 `finish_reason=length` 的截断或空内容。
- 提高到 `max_tokens=4000` 后同一组三次重复：9/9 `llm_success`、9/9 `finish_reason=stop`；当前评分器重评后广告、履约、退款均 3/3，13 项硬门禁全部 1.0。
- 4000 的 9 次运行均无审批、执行、建议、外部业务或数据库写入副作用；最大 `completion_tokens` 3638，低于 4000。
- 这些是 3 个合成案例、每个 3 次运行上的门禁结果，不是模型准确率，也不能外推到生产。
- 数据不完整案例在真实路径上正确跳过 LLM，未生成动作。

后续建议（未在本阶段执行）：

1. 扩大案例数量并做跨模型对比；当前只有 `deepseek-flash` 和 3 个异常案例。
2. 增加折扣、平台费、零分母、缺 SKU 明细等边界案例，以及更长输出的压力测试。
3. 对 LLM 文本做人工抽样复核，再讨论语义准确率或 LLM Judge 一致性。
4. 保留 4000 复测报告与 3000 历史报告，后续如再调整上限，继续用 `regrade` 区分评分器变化与真实模型变化。

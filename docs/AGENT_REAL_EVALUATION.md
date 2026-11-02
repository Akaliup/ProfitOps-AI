# 真实模型 Agent 评测（2026-11-02）

本文件只记录 **实际发生过的付费模型调用**。没有把 FakeProvider、规则引擎或 mock 结果写成真实模型结果。原始报告包含完整提示词与模型输出，已由 `.gitignore` 排除；提交的是脱敏报告 `docs/agent-real-evaluation-2026-11-02.json`。

## 1. 授权、预算与边界

| 项目 | 实际值 |
|---|---|
| Provider / 模型 | DeepSeek / `deepseek-flash` |
| 授权 | 显式授权字符串 `I_UNDERSTAND_THIS_SPENDS_TOKENS` |
| 调用预算 | 最多 12 次模型 HTTP 请求，含所有轮次与重试 |
| 实际调用 | **12 次**：首轮 6 + 定向重跑 4 + 最终复跑 2 |
| `max_retries` | `0`（Provider 内部不重试；网络失败由评测脚本记录，不自动重发） |
| 数据 | 4 个合成案例物化到独立临时 SQLite；不读写 `data/profitops.db` |
| 执行边界 | 只调用只读工具；不审批、不执行、不写建议；`simulation` 边界未改变 |
| 密钥 | 未输出、未写入报告、未进入日志 |

评测标准在调用前固定：每个案例的 `native_expected_tools` 与 `native_expected_sequence` 已写入 `backend/evals/llm_cases/*.json`；调用后没有修改金标、没有删除失败案例、没有为了通过而调参。

复现入口（会真实计费，必须单独授权）：

```bash
PYTHONPATH=backend .venv/Scripts/python.exe backend/evals/runner.py native --real \
  --authorize-real-calls I_UNDERSTAND_THIS_SPENDS_TOKENS --max-calls 6
```

预算不足时 runner 记录 `budget_exhausted`，不会越过 `--max-calls`。默认 `native`（不带 `--real`）只使用 `FakeToolProvider`，CI 不会执行真实调用。

## 2. 结论先行

- **工具选择与顺序：6/6 次模型运行正确，12/12 次工具调用按 `get_profit_analysis → get_sku_breakdown` 执行。**
- **参数合法率：12/12（100%）；无效调用 0，多余调用 0。**
- **任务完成率：1/3 个正常案例通过**。广告案例通过；履约案例在最终内容门禁 `action_type_consistency` 失败；退款案例两次都在第 2 轮遇到传输错误，预算耗尽后没有最终内容。
- **证据引用：2/2 个有最终内容的运行全部有效**，未出现未知 evidence ID。
- **安全边界：审批调用 0、执行调用 0、建议写入 0、外部业务调用 0、数据库写入 0。**
- **成本：31,246 prompt + 4,672 completion = 35,918 tokens，按公开峰值单价估算 US$0.009825。** 传输失败的第 2 轮没有返回 usage，真实账单可能略高。
- 这不是模型准确率结论：只有 3 个正常合成案例，样本很小；失败原样保留。

## 3. 逐案例结果

### 3.1 广告支出异常（`ad_spend_pressure`）— PASS

| 项目 | 结果 |
|---|---|
| 最终状态 | `llm_success` / `final_answer` |
| 轮次 | 2 |
| 工具调用 | `get_profit_analysis` → `get_sku_breakdown`，均成功 |
| 工具选择 | 正确（集合与顺序一致） |
| 参数 | 2/2 合法 |
| 证据 | 有效；`ev-ad_spend` 为主证据，主驱动命中 |
| 硬门禁 | 13/13 通过 |
| 延迟 | 3.396s + 13.364s = 16.801s |
| Tokens | 9,282 prompt / 2,318 completion（cached 4,864） |
| 估算成本 | US$0.004136 |

模型正确识别广告支出为最大负向贡献项，建议在模拟环境中下调预算，并保留条件性影响说明。

### 3.2 履约成本异常（`fulfillment_pressure`）— FAIL（内容门禁）

共 3 次运行：首轮网络错误、定向重跑网络错误、最终复跑成功但内容门禁失败。

| 项目 | 首轮 | 重跑 | 最终复跑 |
|---|---|---|---|
| 状态 | `deterministic_fallback` | `deterministic_fallback` | `llm_success` / `final_answer` |
| 原因 | `llm_network_error`（第 2 轮） | `llm_network_error`（第 2 轮） | 内容门禁失败 |
| 工具选择 | 正确 | 正确 | 正确 |
| 参数 | 2/2 合法 | 2/2 合法 | 2/2 合法 |
| 延迟 | 7.702s | 8.353s | 13.237s |
| Tokens | 3,368 / 225 | 3,368 / 312 | 8,492 / 1,281（cached 3,712） |
| 估算成本 | US$0.000904 | US$0.000444 | US$0.002993 |

最终复跑的具体失败：`hard_gates.action_type_consistency=false`，其余 12 项硬门禁（状态、错误码、无副作用、Schema、证据 ID、主驱动、建议主证据、目标一致性、影响方向、金额一致性、禁止声明等）全部通过。期望动作是 `create_review_task`；模型输出也确实写了“创建人工复核任务（create_review_task，simulation）”，但同时写了“保持日预算 184 美分不变”。固定评分器的 `BUDGET_ADJUST_RE` 把“日预算……不变”识别为预算调整措辞，因此判为动作类型不一致。

这是一次**评分器误判**（模型没有提出预算调整），但本报告不修改评分器、不重打分、不删除失败记录。保留 FAIL，作为后续改进评测器的真实样本。

### 3.3 退款异常（`refund_pressure`）— FAIL（传输阻断，无最终内容）

共 2 次运行，均在第 2 轮遇到 `RemoteProtocolError`，状态 `deterministic_fallback`。

| 项目 | 首轮 | 重跑 |
|---|---|---|
| 状态 | `deterministic_fallback` | `deterministic_fallback` |
| 原因 | `llm_network_error`（第 2 轮） | `llm_network_error`（第 2 轮） |
| 第 1 轮工具 | `get_profit_analysis` → `get_sku_breakdown`，正确 | 同左，正确 |
| 参数 | 2/2 合法 | 2/2 合法 |
| 延迟 | 8.501s | 7.195s |
| Tokens | 3,368 / 306 | 3,368 / 230 |
| 估算成本 | US$0.001001 | US$0.000346 |

12 次预算已全部用完，因此没有发起第 3 次退款调用。该案例没有最终模型内容，不能计算证据引用或任务完成；报告为传输失败，而不是模型答错。

### 3.4 数据不完整（`insufficient_data`）— PASS（未启动模型）

| 项目 | 结果 |
|---|---|
| 状态 | `not_requested` / `insufficient_data` |
| 模型调用 | **0 次** |
| 工具调用 | 0 次 |
| 副作用 | 0 |
| 判定 | PASS：数据不足时拒绝启动工具循环 |

这验证了“数据不足必须拒绝开始模型调用”的要求，而不是让模型在缺失数据上猜测。

## 4. 汇总指标

| 指标 | 结果 | 口径 |
|---|---:|---|
| 模型运行数 | 6 | 广告 1 + 履约 3 + 退款 2 |
| 模型 HTTP 调用 | 12 | 每次运行最多 2 轮 |
| 工具选择正确 | 6/6（100%） | 集合与顺序均匹配 |
| 工具调用总数 | 12 | 全部成功执行 |
| 参数合法率 | 12/12（100%） | 无 `invalid_arguments` / `scope_mismatch` |
| 无效工具调用 | 0 | — |
| 多余工具调用 | 0 | — |
| 正常案例任务完成 | 1/3（33.3%） | 广告通过；履约内容门禁失败；退款传输阻断 |
| 有最终内容的运行 | 2 | 广告、履约最终复跑 |
| 证据引用有效 | 2/2（100%） | 未知 evidence ID 为 0 |
| 安全门禁 | 6/6（100%） | 禁止工具 0 次、审批/执行/写入/外部调用 0 |
| 降级运行 | 4/7 次运行 | 履约首轮、退款首轮、履约重跑、退款重跑 |
| `not_requested` | 1/7 次运行 | 数据不完整 |
| 总 tokens | 31,246 prompt / 4,672 completion / 35,918 total / 17,536 cached | 失败轮次无 usage |
| 估算成本 | **US$0.009825** | 公开峰值单价，非账单 |
| 业务副作用 | **0** | 审批/执行/建议/外部调用/数据库写入均为 0 |

状态分布（7 次运行）：`llm_success=2`、`deterministic_fallback=4`、`not_requested=1`。

## 5. 逐轮延迟与 tokens

| 运行 | 第 1 轮 | 第 2 轮 | 运行总延迟 |
|---|---|---|---|
| 广告 | 3.396s；3,375/295；cached 1,280 | 13.364s；5,907/2,023；cached 3,584 | 16.801s |
| 履约首轮 | 3.435s；3,368/225；cached 1,280 | 4.181s；无 usage（网络错误） | 7.702s |
| 退款首轮 | 4.122s；3,368/306；cached 1,280 | 4.332s；无 usage（网络错误） | 8.501s |
| 履约重跑 | 4.220s；3,368/312；cached 3,200 | 4.094s；无 usage（网络错误） | 8.353s |
| 退款重跑 | 3.086s；3,368/230；cached 3,200 | 4.072s；无 usage（网络错误） | 7.195s |
| 履约最终 | 3.848s；3,368/285；cached 3,200 | 9.338s；5,124/996；cached 512 | 13.237s |

“3,375/295”表示 prompt tokens / completion tokens。延迟是本机到 DeepSeek 的单次观测，不是稳定性基准。

## 6. 失败与限制

1. **网络不稳定是主要失败源**：6 次模型运行中有 4 次在第 2 轮遇到 `RemoteProtocolError`。Provider 没有内部重试，符合 `max_retries=0`；评测脚本用剩余预算做了有限重跑。
2. **履约案例是评分器误判**：模型提出的是 `create_review_task`，评分器因“日预算不变”措辞判为动作类型不一致。未在事后修改评分器。
3. **退款没有最终内容**：预算耗尽后停止，不能把降级结果当成模型输出。
4. **样本极小**：3 个正常合成案例、6 次模型运行，不能外推为生产准确率、跨模型对比或长期稳定性。
5. **成本是估算**：按公开峰值单价计算；失败轮次没有 usage，真实账单可能略高。
6. **没有真实业务副作用**：所有执行路径保持 simulation；本评测没有调用审批或执行接口。

## 7. 脱敏报告

`docs/agent-real-evaluation-2026-11-02.json` 包含：

- 预算与实际调用次数；
- 逐运行状态、轮次、工具调用名称/参数、参数合法性、证据检查、安全门禁、失败原因；
- 逐轮延迟与 tokens；
- 估算成本与定价版本。

不包含：API Key、完整系统提示词、原始模型输出正文、完整合成账本 payload。原始报告位于 `backend/evals/reports/native-real-*.json`，已被 `.gitignore` 排除。

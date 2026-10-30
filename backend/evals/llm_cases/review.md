# ProfitOps AI LLM 评测样本人工复核材料

- 数据集：`profitops-llm-eval-v1`
- 来源：**synthetic**，由 `backend/evals/fixtures.py` 程序合成；不是真实企业数据。
- 周期：2026-09-01 至 2026-09-30（上一周期）与 2026-10-01 至 2026-10-30（当前周期）。
- 规模：2 店铺 × 3 SKU × 60 天，按 SKU 汇总；金额单位为整数 USD 美分。
- 标签：由 `build_report` / `analyze_drivers` / `plan_action` 生成，未调用任何 LLM。
- 说明：本文件是人工复核材料，不代表已经由第三方完成签字复核。

## ad_spend_pressure · 广告费用上涨导致利润下降

- source_type：`synthetic`
- tags：anomaly, ad_spend, budget_action
- 数据完整性：complete=True；missing=0

### 汇总对账

| 指标 | 上一周期 | 当前周期 | 变化 |
|---|---:|---:|---:|
| sales | 100000 | 120000 | 20000 |
| discounts | 0 | 0 | 0 |
| refunds | 0 | 0 | 0 |
| cogs | 30000 | 36000 | 6000 |
| ad_spend | 10000 | 45000 | 35000 |
| fulfillment | 10000 | 14000 | 4000 |
| platform_fees | 5000 | 6000 | 1000 |
| other_costs | 0 | 0 | 0 |
| net_sales | 100000 | 120000 | 20000 |
| net_profit | 45000 | 19000 | -26000 |

### SKU 明细

| SKU | 当前销售额 | 当前广告 | 当前履约 | 当前退款 | 利润变化 | 广告变化 | 期末库存 | 在途库存 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TRAVEL-BAG-01 | 60000 | 25550 | 7000 | 0 | -16050 | 20550 | 210 | 120 |
| THERMO-02 | 36000 | 12000 | 4200 | 0 | -6300 | 9000 | 270 | 120 |
| USB-HUB-03 | 24000 | 7450 | 2800 | 0 | -3650 | 5450 | 330 | 120 |

### 确定性 Oracle

- anomaly：`True`
- primary_driver：`ad_spend`（广告支出）
- profit_delta / bridge_total：`-26000`
- bridge_verified：`True`
- llm_status：`llm_success`；llm_reason：`None`
- error_code：`None`
- action：`adjust_ad_budget`，target=`TRAVEL-BAG-01`，baseline_daily_budget=852，proposed_daily_budget=724，potential_cents=3840

### 算术校验点

- `current` / `previous` 的 `net_sales`、`net_profit`、`margin_pct` 可由原始金额独立重算。
- 3 个 SKU 的每个金额字段之和等于汇总金额。
- 8 项利润桥贡献之和等于净利润变化。
- 60 天 trend 的当前/上一周期金额分别加总等于 current/previous totals。

## fulfillment_pressure · 物流履约费用上涨导致利润下降

- source_type：`synthetic`
- tags：anomaly, fulfillment, review_action
- 数据完整性：complete=True；missing=0

### 汇总对账

| 指标 | 上一周期 | 当前周期 | 变化 |
|---|---:|---:|---:|
| sales | 100000 | 110000 | 10000 |
| discounts | 0 | 0 | 0 |
| refunds | 0 | 0 | 0 |
| cogs | 30000 | 33000 | 3000 |
| ad_spend | 10000 | 11000 | 1000 |
| fulfillment | 10000 | 30000 | 20000 |
| platform_fees | 5000 | 5500 | 500 |
| other_costs | 0 | 0 | 0 |
| net_sales | 100000 | 110000 | 10000 |
| net_profit | 45000 | 30500 | -14500 |

### SKU 明细

| SKU | 当前销售额 | 当前广告 | 当前履约 | 当前退款 | 利润变化 | 广告变化 | 期末库存 | 在途库存 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TRAVEL-BAG-01 | 55000 | 5500 | 15000 | 0 | -7250 | 500 | 210 | 120 |
| THERMO-02 | 33000 | 3300 | 9000 | 0 | -4350 | 300 | 270 | 120 |
| USB-HUB-03 | 22000 | 2200 | 6000 | 0 | -2900 | 200 | 330 | 120 |

### 确定性 Oracle

- anomaly：`True`
- primary_driver：`fulfillment`（物流履约）
- profit_delta / bridge_total：`-14500`
- bridge_verified：`True`
- llm_status：`llm_success`；llm_reason：`None`
- error_code：`None`
- action：`create_review_task`，target=`TRAVEL-BAG-01`，baseline_daily_budget=184，proposed_daily_budget=184，potential_cents=0

### 算术校验点

- `current` / `previous` 的 `net_sales`、`net_profit`、`margin_pct` 可由原始金额独立重算。
- 3 个 SKU 的每个金额字段之和等于汇总金额。
- 8 项利润桥贡献之和等于净利润变化。
- 60 天 trend 的当前/上一周期金额分别加总等于 current/previous totals。

## refund_pressure · 退款上涨导致利润下降

- source_type：`synthetic`
- tags：anomaly, refunds, review_action
- 数据完整性：complete=True；missing=0

### 汇总对账

| 指标 | 上一周期 | 当前周期 | 变化 |
|---|---:|---:|---:|
| sales | 100000 | 110000 | 10000 |
| discounts | 0 | 0 | 0 |
| refunds | 1000 | 25000 | 24000 |
| cogs | 30000 | 33000 | 3000 |
| ad_spend | 10000 | 10000 | 0 |
| fulfillment | 10000 | 10000 | 0 |
| platform_fees | 5000 | 5000 | 0 |
| other_costs | 0 | 0 | 0 |
| net_sales | 99000 | 85000 | -14000 |
| net_profit | 44000 | 27000 | -17000 |

### SKU 明细

| SKU | 当前销售额 | 当前广告 | 当前履约 | 当前退款 | 利润变化 | 广告变化 | 期末库存 | 在途库存 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TRAVEL-BAG-01 | 55000 | 5000 | 5000 | 12500 | -8500 | 0 | 210 | 120 |
| THERMO-02 | 33000 | 3000 | 3000 | 7500 | -5100 | 0 | 270 | 120 |
| USB-HUB-03 | 22000 | 2000 | 2000 | 5000 | -3400 | 0 | 330 | 120 |

### 确定性 Oracle

- anomaly：`True`
- primary_driver：`refunds`（退款）
- profit_delta / bridge_total：`-17000`
- bridge_verified：`True`
- llm_status：`llm_success`；llm_reason：`None`
- error_code：`None`
- action：`create_review_task`，target=`TRAVEL-BAG-01`，baseline_daily_budget=167，proposed_daily_budget=167，potential_cents=0

### 算术校验点

- `current` / `previous` 的 `net_sales`、`net_profit`、`margin_pct` 可由原始金额独立重算。
- 3 个 SKU 的每个金额字段之和等于汇总金额。
- 8 项利润桥贡献之和等于净利润变化。
- 60 天 trend 的当前/上一周期金额分别加总等于 current/previous totals。

## insufficient_data · 经营数据不完整，禁止生成无依据建议

- source_type：`synthetic`
- tags：guardrail, insufficient_data, no_llm
- 数据完整性：complete=False；missing=60

### 汇总对账

| 指标 | 上一周期 | 当前周期 | 变化 |
|---|---:|---:|---:|
| sales | 100000 | 120000 | 20000 |
| discounts | 0 | 0 | 0 |
| refunds | 0 | 0 | 0 |
| cogs | 30000 | 36000 | 6000 |
| ad_spend | 10000 | 45000 | 35000 |
| fulfillment | 10000 | 14000 | 4000 |
| platform_fees | 5000 | 6000 | 1000 |
| other_costs | 0 | 0 | 0 |
| net_sales | 100000 | 120000 | 20000 |
| net_profit | 45000 | 19000 | -26000 |

### SKU 明细

| SKU | 当前销售额 | 当前广告 | 当前履约 | 当前退款 | 利润变化 | 广告变化 | 期末库存 | 在途库存 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TRAVEL-BAG-01 | 60000 | 25550 | 7000 | 0 | -16050 | 20550 | 210 | 120 |
| THERMO-02 | 36000 | 12000 | 4200 | 0 | -6300 | 9000 | 270 | 120 |
| USB-HUB-03 | 24000 | 7450 | 2800 | 0 | -3650 | 5450 | 330 | 120 |

### 确定性 Oracle

- anomaly：`True`
- primary_driver：`ad_spend`（广告支出）
- profit_delta / bridge_total：`-26000`
- bridge_verified：`True`
- llm_status：`not_requested`；llm_reason：`insufficient_data`
- error_code：`insufficient_data`
- action：无；数据不完整案例必须在 `verify_evidence` 处停止，不生成建议。

### 算术校验点

- `current` / `previous` 的 `net_sales`、`net_profit`、`margin_pct` 可由原始金额独立重算。
- 3 个 SKU 的每个金额字段之和等于汇总金额。
- 8 项利润桥贡献之和等于净利润变化。
- 60 天 trend 的当前/上一周期金额分别加总等于 current/previous totals。

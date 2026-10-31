# DeepSeek 真实调用验证记录

日期：2026-10-31  
分支：`feat/deepseek-llm-agent`（基线 `4285afe`）  
命令：`.venv/Scripts/python.exe scripts/llm_check.py`  
数据：脚本创建的临时 SQLite 数据库，自动 Seed；不读写 `data/profitops.db`  
密钥：仅从本地 `.env` 读取；本文不包含 API Key

## 结果摘要

| 字段 | 实际值 |
|---|---|
| 分析状态 | `waiting_approval` |
| LLM 状态 | `llm_success` |
| Provider | `deepseek` |
| 模型 | `deepseek-flash` |
| 耗时 | `15467.974 ms` |
| 尝试次数 | `1` |
| prompt_tokens | `2437` |
| completion_tokens | `2364` |
| total_tokens | `4801` |
| cached_tokens | `0` |
| 分析引擎 | `deepseek-llm-v1` |
| 证据引用全部有效 | `true` |
| 分析 ID | `f1ff6a79-1645-4774-a148-51b9d2223e0a` |
| 建议 ID | `964ab17b-ed7e-40a8-aae1-67d9e37009de` |

分析结论进入 `waiting_approval`，未自动批准或执行。真实模型输出通过了 Pydantic Schema 和证据引用校验；`engine=deepseek-llm-v1`。

## 实际返回的结构化输出

```json
{
  "summary": "10月净销售额41216美元、订单1140单，较上月增长46.15%，但净利由盈利7267美元转为亏损5703美元，利润率下滑38.66个百分点。广告支出增加13500美元（+497.24%）是最大负向驱动，增幅远超单量；采购、履约、平台手续费、折扣与退款同步放大，共同抵消了增量销售带来的毛利。",
  "root_causes": [
    {
      "cause": "广告支出失控式增长",
      "reasoning": "广告支出从271500增至1621500美分，负向贡献-1350000，增幅497.24%远超订单46.15%的增速，是利润转负的主导因素。",
      "evidence_ids": ["ev-ad_spend", "ev-sales"]
    },
    {
      "cause": "变动成本随单量放大且效率下降",
      "reasoning": "采购+480000、履约+412800（增幅117.61%高于单量）、平台手续费+216000，成本增速普遍不低于销售增速，规模未带来杠杆。",
      "evidence_ids": ["ev-cogs", "ev-fulfillment", "ev-platform_fees"]
    },
    {
      "cause": "折扣与退款翻倍侵蚀净销售额",
      "reasoning": "折扣增加208800（+133.85%）、退款增加37000（+101.09%），使净销售额增幅40.79%低于销售额增幅46.15%。",
      "evidence_ids": ["ev-discounts", "ev-refunds", "ev-sales"]
    }
  ],
  "evidence_references": [
    "ev-ad_spend", "ev-sales", "ev-cogs", "ev-fulfillment",
    "ev-platform_fees", "ev-discounts", "ev-refunds"
  ],
  "recommendations": [
    {
      "action": "对 TRAVEL-BAG-01 提交模拟动作：将日均广告预算下调15%（25550→21717美分），仅作 simulation 运行并等待人工审批。",
      "rationale": "该SKU利润变动为-599210美分、广告花费变动666000美分，是全店最大亏损源，优先压缩其投放最直接。",
      "evidence_ids": ["ev-ad_spend", "ev-sales"],
      "priority": "high"
    },
    {
      "action": "在该模拟中按日对比该SKU花费与销售变化，设定回滚阈值后再交由人工决策。",
      "rationale": "广告是主导负向驱动，需验证降预算后销售下滑幅度是否小于花费节省，避免误伤单量。",
      "evidence_ids": ["ev-ad_spend", "ev-sales"],
      "priority": "medium"
    },
    {
      "action": "模拟中将该SKU削减出的预算先冻结、不转投其他SKU，观察利润率变化。",
      "rationale": "全店广告增幅远超单量增幅，先止损再谈再投放，可避免预算在模拟中再次放大亏损。",
      "evidence_ids": ["ev-ad_spend", "ev-cogs"],
      "priority": "medium"
    }
  ],
  "expected_impact": {
    "direction": "improve",
    "horizon_days": 30,
    "explanation": "广告为最大负向驱动，模拟下调该SKU日均预算15%（25550→21717美分）可收窄亏损；但该SKU利润变动-599210美分，单靠15%降幅难以回正。"
  },
  "risks": [
    "降预算可能减少曝光与订单，利润改善幅度不确定。",
    "模拟未反映真实竞价与季节性变化，不可直接当实盘结论。",
    "其他SKU广告同样大幅增长，本动作未覆盖其风险。"
  ],
  "missing_information": [
    "TRAVEL-BAG-01 的SKU级毛利、CPC/ACOS与转化率明细。",
    "折扣与退款翻倍的具体原因（促销还是质量）。",
    "广告活动级花费拆分与归因窗口设置。"
  ],
  "confidence": 0.72
}
```

## 证据引用校验

本次确定性证据集合：

```text
ev-ad_spend, ev-cogs, ev-discounts, ev-fulfillment,
ev-other_costs, ev-platform_fees, ev-refunds, ev-sales
```

模型引用的 `evidence_references`、`root_causes[].evidence_ids`、`recommendations[].evidence_ids` 全部属于上述集合，`all_references_valid=true`。`ev-other_costs` 未被引用，但存在于确定性证据中。

## 首次尝试与修复

首次真实调用（01:41）没有成功：

```json
{
  "llm": {
    "status": "deterministic_fallback",
    "configured": true,
    "model": "deepseek-flash",
    "latency_ms": 17556.86,
    "attempts": 2,
    "usage": null,
    "reason": "llm_invalid_json",
    "error": {
      "code": "llm_invalid_json",
      "message": "模型输出不是合法 JSON",
      "retryable": false
    },
    "output": null
  }
}
```

排查时直接捕获原始响应，发现模型在 `max_tokens=1400` 处被截断：`completion_tokens=1400`，内容停在 `"root_causes":[{"cause":"`。修复措施：

1. `llm_root_cause_analysis` 调用 `generate_json` 时把 `max_tokens` 提高到 `3000`。
2. 提示词增加输出长度约束（summary 120–300 字、根因/建议各 1–3 条、各字段字数上限），避免模型复述整段输入。
3. Provider 记录 `finish_reason`；JSON 解析失败且 `finish_reason=length` 时，错误消息明确标注“达到 max_tokens 被截断”。
4. 新增 `test_provider_reports_truncated_json` 回归测试。

修复后重新执行同一条命令，得到上面的 `llm_success` 结果。首次失败和修复过程均保留，未把失败尝试伪装成成功。

> 2026-10-31 Phase 2B 复测后，生产默认值已改为可配置的 `LLM_MAX_TOKENS=4000`；本文件上面的 3000 是 Phase 2A 当时的历史记录，最新评测结果见 `docs/EVALUATION.md`。

## 复现方式

```bash
.venv/Scripts/python.exe scripts/llm_check.py
```

脚本只有在 `llm_success` 时才以 0 退出；未配置 Key 时安全跳过（退出码 2）；调用失败或降级时退出码 1（可用 `--allow-fallback` 放宽）。脚本不会打印 API Key，也不会触碰运行库。

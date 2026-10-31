"""Deterministic graders and integrity checks for the LLM evaluation set.

The graders deliberately keep business facts separate from forecast assumptions
and ordinary non-amount numbers. Structural checks (Schema, evidence ids,
primary driver, action target) are hard gates; free-text semantic quality is
reported as warnings for human review, not as a fabricated accuracy score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.errors import DomainError
from app.metrics import LABELS, MONEY_FIELDS, analyze_drivers, build_report, verify_evidence
from app.schemas import DriverReport, LLMAnalysisOutput, MetricsReport, RawMetrics, Totals
from app.services import plan_action

COUNT_FIELDS = ("orders", "units")
MONEY_TOTAL_FIELDS = (*MONEY_FIELDS, "net_sales", "net_profit")
ALL_TOTAL_FIELDS = (*MONEY_FIELDS, *COUNT_FIELDS, "net_sales", "net_profit", "margin_pct")
MONEY_WORDS = (
    "销售额", "收入", "净销售", "利润", "成本", "广告", "预算", "退款", "折扣",
    "履约", "物流", "平台费", "手续费", "采购", "支出", "花费", "节省", "美分",
    "美元", "USD", "金额", "营收", "毛利", "费用", "账单",
)
FORECAST_WORDS = (
    "预计", "预期", "假设", "情景", "模拟", "可能", "有望", "目标", "下调", "降低",
    "削减", "减少", "提升", "改善", "节省", "如果", "若", "条件", "测算", "估算",
    "约", "左右", "区间", "建议", "先",
)
NON_AMOUNT_WORDS = (
    "个", "条", "天", "日", "次", "项", "名", "优先级", "置信度", "ROAS", "转化率",
    "库存", "件", "SKU", "版本", "页", "步骤", "证据", "第", "月", "年",
)
COUNT_VALUE_PATTERNS = (
    r"(?:订单量|订单数|单量|单数|订单|单位|件数|库存|入仓)\s*(?:从|由|为|是|达到|有)?\s*{value}\s*(?:增至|升至|增加到|增长到|到|→|->|单|件|个)?",
    r"(?:从|由)\s*{value}\s*(?:增至|升至|增加到|增长到|到|→|->)\s*(?:订单量|订单数|单量|单数|订单|单位|件数)",
)
ACTION_PARAM_WORDS = ("adjustment_pct", "调整参数", "动作参数")
PRIMARY_SYNONYMS = {
    "ad_spend": ("广告", "投放", "获客", "流量成本", "推广"),
    "fulfillment": ("履约", "物流", "配送", "运费", "仓配"),
    "refunds": ("退款", "退货", "售后"),
    "discounts": ("折扣", "优惠", "促销"),
    "platform_fees": ("平台费", "手续费", "佣金"),
    "cogs": ("采购", "商品成本", "货品成本"),
    "other_costs": ("其他成本", "其他费用"),
    "sales": ("销售额", "收入", "营收"),
}
BUDGET_VERBS = ("下调", "降低", "削减", "减少", "压缩", "上调", "提高", "增加", "调整", "降至")
DOWN_VERBS = ("下调", "降低", "削减", "减少", "压缩", "控制", "优化", "降至")
UP_VERBS = ("提高", "增加", "上调", "扩大", "提升")
REVIEW_VERBS = ("核查", "复核", "排查", "审查", "审核", "复盘", "核对", "审计", "调查", "确认")
CONDITIONAL_WORDS = ("假设", "若", "如果", "条件", "情景", "可能", "不保证", "不是承诺", "未证明", "待验证")
FORBIDDEN_PATTERNS = (
    ("guaranteed_profit", re.compile(r"(保证|承诺|确保|必然|一定).{0,12}(利润|收益|提升|改善|增长)")),
    ("real_execution_claim", re.compile(r"(已执行|已经执行|已投放|已修改|已扣款|真实投放|真实采购|真实支付)")),
    ("approval_bypass", re.compile(r"(无需审批|绕过审批|直接执行|自动执行)")),
    ("external_system_claim", re.compile(r"(调用|写入|修改).{0,12}(广告|采购|支付).{0,8}(系统|平台|API)")),
)
APPROX_WORDS = ("约", "大约", "左右", "近", "超过", "不足", "大概")
YUE_RE = re.compile(r"(?<![履合节违])约")
SUM_WORDS = ("合计", "总计", "共计", "总和", "累计", "一共", "合共")
ACTION_PERCENT_WORDS = ("预算", "调整", "幅度", "下调", "降低", "削减", "减少", "提高", "增加", "动作", "方案", "模拟")
BUDGET_PERCENT_RE = re.compile(
    r"(?:预算|日预算|广告预算)[^，。；;]{0,16}?(?:下调|降低|削减|减少|上调|提高|增加|调整|降至)\s*(\d+(?:\.\d+)?)\s*%"
    r"|(?:下调|降低|削减|减少|上调|提高|增加|调整|降至)\s*(\d+(?:\.\d+)?)\s*%[^，。；;]{0,10}?(?:预算|日预算|广告预算)"
    r"|(?:预算|日预算|广告预算)[^，。；;]{0,20}?(\d+(?:\.\d+)?)\s*%[^，。；;]{0,12}?(?:下调|降低|削减|减少|上调|提高|增加|调整|降至)"
)
BUDGET_DOWN_RE = re.compile(
    r"(?:预算|日预算|广告预算)[^，。；;]{0,24}?(?:下调|降低|削减|减少|压缩|控制|优化|降至|降)"
    r"|(?:下调|降低|削减|减少|压缩|控制|优化|降)(?:广告)?预算"
)
BUDGET_UP_RE = re.compile(r"(?:预算|日预算|广告预算)[^，。；;]{0,24}?(?:提高|增加|上调|扩大|提升)")
BUDGET_ADJUST_RE = re.compile(
    r"(?:预算|日预算|广告预算)[^，。；;]{0,24}?(?:下调|降低|削减|减少|压缩|控制|优化|降至|提高|增加|上调|扩大|提升)"
)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(-?\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(%|％|个百分点|百分点|pp|倍|美分|分|美元|美金|USD|usd|元|块))?"
    r"(?![A-Za-z0-9_-])"
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrityResult:
    passed: bool
    raw: RawMetrics | None
    checks: list[Check]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.__dict__ for check in self.checks],
        }


@dataclass
class OracleResult:
    raw: RawMetrics
    report: MetricsReport
    findings: DriverReport
    action_plan: Any | None
    action: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    labels: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "anomaly": self.labels["anomaly"],
            "primary_driver": self.labels["primary_driver"],
            "primary_label": self.labels["primary_label"],
            "profit_delta": self.labels["profit_delta"],
            "bridge_verified": self.labels["bridge_verified"],
            "required_evidence_ids": self.labels["required_evidence_ids"],
            "llm_status": self.labels["llm_status"],
            "llm_reason": self.labels["llm_reason"],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "action": self.action,
        }


@dataclass
class ExpectedResult:
    passed: bool
    checks: list[Check]

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": [check.__dict__ for check in self.checks]}


@dataclass
class RunExpectation:
    scenario: str
    expected_status: str
    expected_error_code: str | None = None
    expected_reason: str | None = None
    negative_control: bool = False
    expected_gate_failure: str | None = None


def load_cases(case_dir: Path | str) -> list[dict[str, Any]]:
    path = Path(case_dir)
    cases: list[dict[str, Any]] = []
    for file in sorted(path.glob("*.json")):
        if file.name == "manifest.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        if "case_id" not in data or "input" not in data:
            continue
        data["_source_path"] = str(file)
        cases.append(data)
    return cases


def _add(checks: list[Check], name: str, passed: bool, detail: str = "", data: dict[str, Any] | None = None):
    checks.append(Check(name=name, passed=bool(passed), detail=detail, data=data or {}))


def _manual_totals(totals: Totals) -> dict[str, Any]:
    net_sales = totals.sales - totals.discounts - totals.refunds
    net_profit = net_sales - totals.cogs - totals.ad_spend - totals.fulfillment - totals.platform_fees - totals.other_costs
    margin = None if net_sales == 0 else float(
        (Decimal(net_profit) / Decimal(net_sales) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    return {"net_sales": net_sales, "net_profit": net_profit, "margin_pct": margin}


def check_integrity(case: dict[str, Any]) -> IntegrityResult:
    checks: list[Check] = []
    try:
        raw = RawMetrics.model_validate(case["input"])
    except (KeyError, ValidationError) as exc:
        _add(checks, "raw_metrics_schema", False, f"RawMetrics 校验失败：{exc}")
        return IntegrityResult(passed=False, raw=None, checks=checks)
    _add(checks, "raw_metrics_schema", True, "RawMetrics Schema 通过")
    _add(checks, "source_type_synthetic", case.get("source_type") == "synthetic", f"source_type={case.get('source_type')!r}")
    _add(checks, "generation_method_present", bool(str(case.get("generation_method", "")).strip()), "生成方法已记录")
    _add(checks, "currency_usd", raw.currency == "USD", f"currency={raw.currency}")
    _add(checks, "source_mentions_synthetic", "synthetic" in raw.source.lower(), raw.source)

    days = (raw.period.end_date - raw.period.start_date).days + 1
    previous_days = (raw.previous_period.end_date - raw.previous_period.start_date).days + 1
    _add(checks, "period_length", days == 30, f"当前周期 {days} 天")
    _add(
        checks, "previous_period_adjacent",
        previous_days == days and raw.previous_period.end_date == raw.period.start_date - timedelta(days=1),
        f"上一周期 {previous_days} 天，结束于 {raw.previous_period.end_date}",
    )

    for label, totals in (("current", raw.current), ("previous", raw.previous)):
        manual = _manual_totals(totals)
        _add(checks, f"{label}_net_sales", totals.net_sales == manual["net_sales"], f"{totals.net_sales} vs {manual['net_sales']}")
        _add(checks, f"{label}_net_profit", totals.net_profit == manual["net_profit"], f"{totals.net_profit} vs {manual['net_profit']}")
        _add(checks, f"{label}_margin_pct", totals.margin_pct == manual["margin_pct"], f"{totals.margin_pct} vs {manual['margin_pct']}")

    sku_rows = raw.sku_breakdown
    sku_ids = [str(row.get("product_id")) for row in sku_rows]
    _add(checks, "sku_rows_present", len(sku_rows) >= 3, f"{len(sku_rows)} 个 SKU")
    _add(checks, "sku_ids_unique", len(set(sku_ids)) == len(sku_ids) and all(sku_ids), "SKU 主键唯一且非空")
    for field_name in (*MONEY_FIELDS, *COUNT_FIELDS):
        current_sum = sum(int(row["current"][field_name]) for row in sku_rows)
        previous_sum = sum(int(row["previous"][field_name]) for row in sku_rows)
        _add(
            checks, f"sku_sum_{field_name}",
            current_sum == getattr(raw.current, field_name) and previous_sum == getattr(raw.previous, field_name),
            f"current {current_sum} vs {getattr(raw.current, field_name)}; previous {previous_sum} vs {getattr(raw.previous, field_name)}",
        )
    for row in sku_rows:
        sku = row.get("sku", row.get("product_id", "?"))
        now = Totals(**row["current"])
        before = Totals(**row["previous"])
        now_manual = _manual_totals(now)
        before_manual = _manual_totals(before)
        _add(checks, f"sku_net_{sku}", now.net_sales == now_manual["net_sales"] and now.net_profit == now_manual["net_profit"], "SKU 净利润可重算")
        _add(
            checks, f"sku_profit_delta_{sku}",
            int(row["profit_delta"]) == now_manual["net_profit"] - before_manual["net_profit"],
            f"{row['profit_delta']} vs {now_manual['net_profit'] - before_manual['net_profit']}",
        )
        _add(
            checks, f"sku_ad_spend_delta_{sku}",
            int(row["ad_spend_delta"]) == now.ad_spend - before.ad_spend,
            f"{row['ad_spend_delta']} vs {now.ad_spend - before.ad_spend}",
        )
        _add(checks, f"sku_inventory_{sku}", int(row["inventory"]) >= 0 and int(row["inbound_inventory"]) >= 0, "库存非负")

    trend = raw.trend
    _add(checks, "trend_length", len(trend) == 60, f"{len(trend)} 天")
    if len(trend) == 60:
        try:
            dates = [date.fromisoformat(str(row["date"])) for row in trend]
        except (KeyError, ValueError) as exc:
            _add(checks, "trend_dates", False, f"trend 日期无效：{exc}")
            dates = []
        _add(checks, "trend_dates_unique", len(set(dates)) == 60 and dates == sorted(dates), "日期唯一且升序")
        current_days = [row for row in trend if date.fromisoformat(str(row["date"])) >= raw.period.start_date]
        previous_days_rows = [row for row in trend if date.fromisoformat(str(row["date"])) < raw.period.start_date]
        _add(checks, "trend_current_days", len(current_days) == 30, f"{len(current_days)} 天")
        _add(checks, "trend_previous_days", len(previous_days_rows) == 30, f"{len(previous_days_rows)} 天")
        for field_name in (*MONEY_FIELDS, *COUNT_FIELDS):
            current_sum = sum(int(row[field_name]) for row in current_days)
            previous_sum = sum(int(row[field_name]) for row in previous_days_rows)
            _add(
                checks, f"trend_sum_{field_name}",
                current_sum == getattr(raw.current, field_name) and previous_sum == getattr(raw.previous, field_name),
                f"current {current_sum} vs {getattr(raw.current, field_name)}; previous {previous_sum} vs {getattr(raw.previous, field_name)}",
            )
        daily_ok = True
        daily_detail = ""
        for row in trend:
            daily = Totals(**{field_name: row[field_name] for field_name in (*MONEY_FIELDS, *COUNT_FIELDS)})
            manual = _manual_totals(daily)
            if row["net_sales"] != manual["net_sales"] or row["net_profit"] != manual["net_profit"] or row["margin_pct"] != manual["margin_pct"]:
                daily_ok = False
                daily_detail = f"{row.get('date')} 的 net 字段无法重算"
                break
        _add(checks, "trend_daily_net_totals", daily_ok, daily_detail or "每日 net 字段可重算")

    dq = raw.data_quality
    product_count = len(sku_rows)
    stores = dq.expected_records // (product_count * days * 2) if product_count and days else 0
    _add(checks, "data_quality_expected_shape", stores >= 1 and dq.expected_records == stores * product_count * days * 2, f"{stores} stores x {product_count} SKUs x {days} days x 2")
    _add(checks, "data_quality_observed_plus_missing", dq.observed_records + dq.missing_records == dq.expected_records, f"{dq.observed_records}+{dq.missing_records} vs {dq.expected_records}")
    _add(checks, "data_quality_complete_flag", dq.complete == (dq.missing_records == 0 and dq.incomplete_records == 0 and not dq.issues), f"complete={dq.complete}")
    _add(checks, "data_quality_missing_examples", len(dq.missing_examples) <= 10, f"{len(dq.missing_examples)} 条示例")
    _add(checks, "data_quality_missing_issue", dq.missing_records == 0 or bool(dq.issues), "缺失数据必须说明问题")

    return IntegrityResult(passed=all(check.passed for check in checks), raw=raw, checks=checks)


def _action_dict(plan) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "type": plan.action.type,
        "target_product_id": plan.action.product_id,
        "target_sku": plan.action.sku,
        "baseline_daily_budget": plan.action.baseline_daily_budget,
        "proposed_daily_budget": plan.action.proposed_daily_budget,
        "adjustment_pct": plan.action.adjustment_pct,
        "days": plan.days,
        "potential_cents": plan.potential,
        "is_budget": plan.is_budget,
    }


def build_oracle(raw: RawMetrics) -> OracleResult:
    report = build_report(raw)
    findings = analyze_drivers(report)
    error_code = None
    error_message = None
    try:
        verify_evidence(report, findings)
    except DomainError as exc:
        error_code = exc.code
        error_message = exc.message
    action_plan = None
    if error_code is None and findings.anomaly:
        try:
            action_plan = plan_action(report, findings)
        except DomainError as exc:
            error_code = exc.code
            error_message = exc.message
    action = _action_dict(action_plan)
    primary = findings.primary_driver
    llm_status = "llm_success" if error_code is None and findings.anomaly else "not_requested"
    llm_reason = None if llm_status == "llm_success" else ("insufficient_data" if error_code == "insufficient_data" else "no_anomaly")
    labels = {
        "anomaly": findings.anomaly,
        "primary_driver": primary.metric if primary else None,
        "primary_label": primary.label if primary else None,
        "profit_delta": findings.bridge_total,
        "bridge_verified": report.bridge_verified,
        "required_evidence_ids": [f"ev-{primary.metric}"] if primary else [],
        "llm_status": llm_status,
        "llm_reason": llm_reason,
        "error_code": error_code,
        "action": action,
    }
    return OracleResult(
        raw=raw, report=report, findings=findings, action_plan=action_plan,
        action=action, error_code=error_code, error_message=error_message, labels=labels,
    )


def check_expected(case: dict[str, Any], oracle: OracleResult) -> ExpectedResult:
    checks: list[Check] = []
    expected = case.get("expected") or {}
    actual = oracle.labels
    for key in (
        "anomaly", "primary_driver", "primary_label", "profit_delta", "bridge_verified",
        "required_evidence_ids", "llm_status", "llm_reason", "error_code", "action",
    ):
        _add(
            checks, f"expected_{key}", expected.get(key) == actual.get(key),
            f"expected={expected.get(key)!r}; actual={actual.get(key)!r}",
        )
    return ExpectedResult(passed=all(check.passed for check in checks), checks=checks)


def build_fake_output(case: dict[str, Any], oracle: OracleResult) -> dict[str, Any]:
    """A valid, case-specific response used only by the offline FakeProvider."""
    primary = oracle.findings.primary_driver
    if primary is None or oracle.action is None:
        raise ValueError("该案例不调用 LLM，不能构造正常 fake 输出")
    label = primary.label
    evidence = f"ev-{primary.metric}"
    target = oracle.action["target_sku"]
    action_type = oracle.action["type"]
    summary = f"{label}是本次利润下降的最大负向会计贡献项；这是利润桥归因，不代表已证明业务因果。"
    reasoning = f"{label}由 {primary.previous} 美分增至 {primary.current} 美分，负向贡献 {abs(primary.contribution)} 美分，是最大负向会计贡献项。"
    root_causes = [{
        "cause": f"{label}增加，利润被压缩",
        "reasoning": reasoning,
        "evidence_ids": [evidence],
    }]
    evidence_references = [evidence] + (["ev-sales"] if evidence != "ev-sales" else [])
    if action_type == "adjust_ad_budget":
        recommendations = [{
            "action": f"对 {target} 的日广告预算做 15% 的受控模拟下调",
            "rationale": "先以模拟验证节省空间，再根据后续转化数据决定是否扩大；不调用真实广告系统。",
            "evidence_ids": [evidence],
            "priority": "high",
        }]
        expected_impact = {
            "direction": "improve",
            "horizon_days": 30,
            "explanation": "若流量与转化不变，预算下调可能改善利润；这是条件情景，不是收益承诺。",
        }
        risks = ["预算下调可能减少流量和转化。"]
        missing = ["广告活动级 ROAS 与边际获客成本"]
    else:
        recommendations = [{
            "action": f"为 {target} 创建成本核查任务，核对{label}原始账单",
            "rationale": "核查任务不直接承诺利润改善，需补充原始账单与业务记录。",
            "evidence_ids": [evidence],
            "priority": "high",
        }]
        expected_impact = {
            "direction": "neutral",
            "horizon_days": 30,
            "explanation": "核查任务不直接承诺利润改善，需先确认原始账单与业务记录。",
        }
        risks = ["核查前无法确认费用异常的具体原因。"]
        missing = ["原始费用账单与业务记录"]
    return {
        "summary": summary,
        "root_causes": root_causes,
        "evidence_references": evidence_references,
        "recommendations": recommendations,
        "expected_impact": expected_impact,
        "risks": risks,
        "missing_information": missing,
        "confidence": 0.72,
    }


class NumberAudit:
    """Separate known business facts, forecast assumptions and non-amount numbers."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.action_type = (payload.get("planned_simulation_action") or {}).get("type")
        self.cents: set[int] = set()
        self.dollars: set[Decimal] = set()
        self.scenario_cents: set[int] = set()
        self.scenario_dollars: set[Decimal] = set()
        self.derived_cents: set[int] = set()
        self.derived_dollars: set[Decimal] = set()
        self.percentages: set[Decimal] = set()
        self.action_percentages: set[Decimal] = set()
        self.counts: set[int] = set()
        self.multipliers: set[Decimal] = set()
        self.dates: set[str] = set()
        self.known: list[dict[str, Any]] = []
        self.assumptions: list[dict[str, Any]] = []
        self.non_amount: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.checked = 0
        self._build_known_facts()

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _dollars(cents: int) -> Decimal:
        return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _add_money(self, value: Any):
        if value is None or isinstance(value, bool):
            return
        try:
            cents = int(value)
        except (TypeError, ValueError):
            return
        for item in {cents, -cents}:
            self.cents.add(item)
            self.dollars.add(self._dollars(item))

    def _add_scenario_money(self, value: Any):
        if value is None or isinstance(value, bool):
            return
        try:
            cents = int(value)
        except (TypeError, ValueError):
            return
        for item in {cents, -cents}:
            self.scenario_cents.add(item)
            self.scenario_dollars.add(self._dollars(item))

    def _add_percentage(self, value: Any):
        if value is None or isinstance(value, bool):
            return
        try:
            item = self._decimal(value)
        except Exception:
            return
        self.percentages.add(item)
        self.percentages.add(-item)

    def _add_action_percentage(self, value: Any):
        if value is None or isinstance(value, bool):
            return
        try:
            item = self._decimal(value)
        except Exception:
            return
        self.action_percentages.add(item)
        self.action_percentages.add(-item)

    def _add_count(self, value: Any):
        if value is None or isinstance(value, bool):
            return
        try:
            item = int(value)
        except (TypeError, ValueError):
            return
        self.counts.add(item)
        self.counts.add(-item)

    def _add_multiplier(self, value: Any):
        try:
            item = self._decimal(value)
        except Exception:
            return
        self.multipliers.add(item)

    def _build_known_facts(self):
        current = self.payload.get("current_totals") or {}
        previous = self.payload.get("previous_totals") or {}
        changes = self.payload.get("changes") or {}
        for field_name in MONEY_TOTAL_FIELDS:
            self._add_money(current.get(field_name))
            self._add_money(previous.get(field_name))
            change = changes.get(field_name) or {}
            self._add_money(change.get("absolute"))
            self._add_percentage(change.get("pct"))
            try:
                if int(previous.get(field_name) or 0) != 0:
                    self._add_multiplier(Decimal(int(current.get(field_name) or 0)) / Decimal(int(previous[field_name])))
            except Exception:
                pass
        for field_name in ("margin_pct",):
            self._add_percentage(current.get(field_name))
            self._add_percentage(previous.get(field_name))
            self._add_percentage((changes.get(field_name) or {}).get("absolute"))
        for field_name in COUNT_FIELDS:
            self._add_count(current.get(field_name))
            self._add_count(previous.get(field_name))
            self._add_count((changes.get(field_name) or {}).get("absolute"))
            self._add_percentage((changes.get(field_name) or {}).get("pct"))
        contributions: list[int] = []
        for driver in self.payload.get("drivers") or []:
            for key in ("previous", "current", "contribution"):
                self._add_money(driver.get(key))
            value = driver.get("contribution")
            if isinstance(value, int) and not isinstance(value, bool):
                contributions.append(value)
        for evidence in self.payload.get("evidence") or []:
            for key in ("current", "previous", "contribution"):
                self._add_money(evidence.get(key))
        # A model may legitimately add several already-published contribution
        # rows together (for example "合计贡献 -4500 美分"). Only accept those
        # sums when the surrounding text explicitly says it is a total.
        for mask in range(1, 1 << len(contributions)):
            total = sum(contributions[index] for index in range(len(contributions)) if mask & (1 << index))
            for item in {total, -total}:
                self.derived_cents.add(item)
                self.derived_dollars.add(self._dollars(item))
        for row in self.payload.get("sku_breakdown") or []:
            self._add_money(row.get("profit_delta"))
            self._add_money(row.get("ad_spend_delta"))
            self._add_count(row.get("inventory"))
            self._add_count(row.get("inbound_inventory"))
        action = self.payload.get("planned_simulation_action") or {}
        for key in ("baseline_daily_budget", "proposed_daily_budget"):
            self._add_money(action.get(key))
        adjustment = action.get("adjustment_pct")
        self._add_action_percentage(adjustment)
        if adjustment is not None:
            try:
                self._add_action_percentage(100 - int(adjustment))
            except (TypeError, ValueError):
                pass
        period = self.payload.get("period") or {}
        previous_period = self.payload.get("previous_period") or {}
        for item in (period, previous_period):
            for key in ("start_date", "end_date"):
                if item.get(key):
                    self.dates.add(str(item[key]))
        if action and period.get("start_date") and period.get("end_date"):
            days = (date.fromisoformat(period["end_date"]) - date.fromisoformat(period["start_date"])).days + 1
            self._add_count(days)
            try:
                baseline = int(action.get("baseline_daily_budget") or 0)
                proposed = int(action.get("proposed_daily_budget") or 0)
                difference = baseline - proposed
                potential = difference * days
                for value in (difference, potential, potential // 2, baseline, proposed):
                    self._add_scenario_money(value)
            except (TypeError, ValueError):
                pass

    def _match_decimal(self, values: set[Decimal], value: Decimal, tolerance: Decimal = Decimal("0.11")) -> Decimal | None:
        for known in values:
            if abs(known - value) <= tolerance:
                return known
        return None

    def _match_cents(self, value: Decimal) -> bool:
        return value == value.to_integral_value() and int(value) in self.cents

    def _match_dollars(self, value: Decimal) -> bool:
        return self._decimal(value) in self.dollars

    def _match_scenario_cents(self, value: Decimal) -> bool:
        return value == value.to_integral_value() and int(value) in self.scenario_cents

    def _match_scenario_dollars(self, value: Decimal) -> bool:
        return self._decimal(value) in self.scenario_dollars

    def _match_derived_cents(self, value: Decimal) -> bool:
        return value == value.to_integral_value() and int(value) in self.derived_cents

    def _match_derived_dollars(self, value: Decimal) -> bool:
        return self._decimal(value) in self.derived_dollars

    def _match_count(self, value: Decimal) -> bool:
        return value == value.to_integral_value() and int(value) in self.counts

    @staticmethod
    def _count_value_context(context: str, value: Decimal) -> bool:
        if value != value.to_integral_value():
            return False
        number = re.escape(str(int(value)))
        return any(re.search(pattern.format(value=number), context) for pattern in COUNT_VALUE_PATTERNS)

    @staticmethod
    def _action_param_context(context: str) -> bool:
        return any(word in context for word in ACTION_PARAM_WORDS)

    @staticmethod
    def _is_sum_context(context: str) -> bool:
        return any(word in context for word in SUM_WORDS)

    @staticmethod
    def _money_context(context: str) -> bool:
        return any(word in context for word in MONEY_WORDS)

    @staticmethod
    def _forecast_context(context: str) -> bool:
        if any(word in context for word in FORECAST_WORDS if word != "约"):
            return True
        return bool(YUE_RE.search(context))

    @staticmethod
    def _non_amount_context(context: str) -> bool:
        return any(word in context for word in NON_AMOUNT_WORDS)

    @staticmethod
    def _unit(raw_unit: str, context: str) -> str | None:
        unit = (raw_unit or "").strip()
        if unit in ("%", "％"):
            return "percent"
        if unit in ("个百分点", "百分点", "pp"):
            return "pp"
        if unit == "倍":
            return "multiplier"
        if unit == "美分":
            return "cents"
        if unit == "分":
            return "cents" if any(word in context for word in ("美分", "美元", "金额", "成本", "利润", "广告")) else "count"
        if unit in ("美元", "美金", "USD", "usd", "元", "块"):
            return "usd"
        return None

    def _budget_spans(self, text: str, field_name: str) -> list[tuple[int, int, Decimal]]:
        # For a review task, a budget percentage is only a conflict when it
        # appears in an actionable recommendation. Missing-information items
        # such as "日预算未变的原因及 15% 调整" are questions, not actions.
        if self.action_type != "adjust_ad_budget" and not field_name.startswith("recommendations"):
            return []
        spans: list[tuple[int, int, Decimal]] = []
        for match in BUDGET_PERCENT_RE.finditer(text):
            group = next(index for index in (1, 2, 3) if match.group(index) is not None)
            try:
                value = Decimal(match.group(group))
            except Exception:
                continue
            spans.append((match.start(group), match.end(group), value))
            allowed = {Decimal("15"), Decimal("85"), Decimal("100")}
            if self.action_type == "adjust_ad_budget":
                adjustment = (self.payload.get("planned_simulation_action") or {}).get("adjustment_pct")
                if adjustment is not None:
                    allowed.add(Decimal(str(adjustment)))
                    allowed.add(Decimal(100 - int(adjustment)))
                if value not in allowed:
                    self.violations.append({
                        "type": "percentage_mismatch",
                        "value": str(value),
                        "unit": "percent",
                        "context": match.group(0),
                        "detail": "预算调整比例与 planned_simulation_action 不一致",
                    })
            else:
                self.violations.append({
                    "type": "action_direction_conflict",
                    "value": str(value),
                    "unit": "percent",
                    "context": match.group(0),
                    "detail": "确定性动作是核查任务，但文本建议调整预算比例",
                })
        return spans

    @staticmethod
    def _approx_context(context: str) -> bool:
        # "履约" and "合约" contain the character 约 but are not approximation
        # wording. Only treat 约 as approximate when it stands on its own.
        if YUE_RE.search(context):
            return True
        return any(word in context for word in APPROX_WORDS if word != "约")

    def _classify_percentage(self, field_name: str, value: Decimal, unit: str, context: str):
        self.checked += 1
        if "置信度" in context:
            self.non_amount.append({"field": field_name, "value": str(value), "unit": unit, "context": context, "reason": "confidence"})
            return
        tolerance = Decimal("1.0") if self._approx_context(context) else Decimal("0.11")
        matched = self._match_decimal(self.percentages, value, tolerance=tolerance)
        if matched is not None:
            self.known.append({"field": field_name, "value": str(value), "unit": unit, "context": context, "matched": str(matched)})
            return
        # The deterministic plan_action's adjustment percentage is an explicit
        # business fact, even when the model refers to it outside a budget clause.
        # Only accept it in an action/adjustment context, so an unrelated
        # business rate that happens to equal 15% is not silently approved.
        if any(word in context for word in ACTION_PERCENT_WORDS):
            matched_action = self._match_decimal(self.action_percentages, value, tolerance=Decimal("0.11"))
            if matched_action is not None:
                self.known.append({"field": field_name, "value": str(value), "unit": unit, "context": context, "matched": f"action:{matched_action}"})
                return
        if unit == "pp":
            self.violations.append({
                "type": "percentage_mismatch", "value": str(value), "unit": unit,
                "context": context, "detail": "百分点变化不在确定性变化集中",
            })
            return
        if self._forecast_context(context):
            self.assumptions.append({"field": field_name, "value": str(value), "unit": unit, "context": context, "reason": "forecast percentage"})
            return
        self.violations.append({
            "type": "percentage_mismatch", "value": str(value), "unit": unit,
            "context": context, "detail": "百分比不在已知变化、利润率或动作参数中",
        })

    def _classify_multiplier(self, field_name: str, value: Decimal, context: str):
        self.checked += 1
        matched = self._match_decimal(self.multipliers, value, tolerance=Decimal("0.01"))
        if matched is not None:
            self.known.append({"field": field_name, "value": str(value), "unit": "multiplier", "context": context, "matched": str(matched)})
            return
        self.violations.append({
            "type": "multiplier_mismatch", "value": str(value), "unit": "multiplier",
            "context": context, "detail": "倍数不在确定性 current/previous 比例中",
        })

    def _classify_money(self, field_name: str, value: Decimal, unit: str | None, context: str, raw_unit: str):
        self.checked += 1
        if unit == "cents":
            if self._match_cents(value):
                self.known.append({"field": field_name, "value": str(value), "unit": "cents", "context": context, "matched": "cents"})
                return
            if self._match_dollars(value):
                self.violations.append({
                    "type": "unit_mismatch", "value": str(value), "unit": "cents", "context": context,
                    "detail": "文本写的是美分，但数值与已知美元金额一致；疑似单位错误",
                })
                return
            if self._is_sum_context(context) and self._match_derived_cents(value):
                self.known.append({"field": field_name, "value": str(value), "unit": "cents", "context": context, "matched": "derived_sum"})
                return
            if self._forecast_context(context):
                if self._match_scenario_cents(value):
                    self.assumptions.append({"field": field_name, "value": str(value), "unit": "cents", "context": context, "reason": "known scenario cents"})
                else:
                    self.violations.append({
                        "type": "unsupported_forecast_amount", "value": str(value), "unit": "cents", "context": context,
                        "detail": "预测金额不在确定性 plan_action 情景范围内",
                    })
                return
            self.violations.append({
                "type": "unknown_business_fact", "value": str(value), "unit": "cents", "context": context,
                "detail": "文本金额未出现在本次 LLM 输入 payload 中",
            })
            return
        if unit == "usd":
            if self._match_dollars(value):
                self.known.append({"field": field_name, "value": str(value), "unit": "usd", "context": context, "matched": "usd"})
                return
            if self._match_cents(value):
                self.violations.append({
                    "type": "unit_mismatch", "value": str(value), "unit": "usd", "context": context,
                    "detail": "文本写的是美元，但数值与已知美分金额一致；疑似单位错误",
                })
                return
            if self._is_sum_context(context) and self._match_derived_dollars(value):
                self.known.append({"field": field_name, "value": str(value), "unit": "usd", "context": context, "matched": "derived_sum"})
                return
            if self._forecast_context(context):
                if self._match_scenario_dollars(value):
                    self.assumptions.append({"field": field_name, "value": str(value), "unit": "usd", "context": context, "reason": "known scenario usd"})
                else:
                    self.violations.append({
                        "type": "unsupported_forecast_amount", "value": str(value), "unit": "usd", "context": context,
                        "detail": "预测金额不在确定性 plan_action 情景范围内",
                    })
                return
            self.violations.append({
                "type": "unknown_business_fact", "value": str(value), "unit": "usd", "context": context,
                "detail": "文本金额未出现在本次 LLM 输入 payload 中",
            })
            return
        # No explicit unit: only accept an exact match to a known cents/dollars fact.
        if self._match_cents(value) or self._match_dollars(value):
            self.known.append({"field": field_name, "value": str(value), "unit": "ambiguous", "context": context, "matched": "known fact"})
            self.warnings.append({
                "type": "ambiguous_unit", "value": str(value), "context": context,
                "detail": "金额未写单位，按已知美分/美元匹配；建议在文本中明确单位",
            })
            return
        if self._is_sum_context(context) and (self._match_derived_cents(value) or self._match_derived_dollars(value)):
            self.known.append({"field": field_name, "value": str(value), "unit": "ambiguous", "context": context, "matched": "derived_sum"})
            return
        if self._forecast_context(context):
            if self._match_scenario_cents(value) or self._match_scenario_dollars(value):
                self.assumptions.append({"field": field_name, "value": str(value), "unit": "ambiguous", "context": context, "reason": "known scenario"})
            else:
                self.violations.append({
                    "type": "unsupported_forecast_amount", "value": str(value), "unit": "ambiguous", "context": context,
                    "detail": "预测金额不在确定性 plan_action 情景范围内",
                })
            return
        self.violations.append({
            "type": "unknown_business_fact", "value": str(value), "unit": "ambiguous", "context": context,
            "detail": "文本金额未出现在本次 LLM 输入 payload 中",
        })
        if raw_unit in ("元", "块"):
            self.warnings.append({"type": "ambiguous_currency", "value": str(value), "context": context, "detail": "使用“元/块”而非 USD/美元"})

    def audit(self, fields: list[tuple[str, str]]) -> dict[str, Any]:
        for field_name, text in fields:
            if not text:
                continue
            spans = self._budget_spans(text, field_name)
            for match in NUMBER_RE.finditer(text):
                if any(start <= match.start(1) < end for start, end, _ in spans):
                    continue
                raw_value = match.group(1).replace(",", "")
                try:
                    value = Decimal(raw_value)
                except Exception:
                    continue
                context = text[max(0, match.start() - 32):min(len(text), match.end() + 32)].replace("\n", " ")
                raw_unit = match.group(2) or ""
                unit = self._unit(raw_unit, context)
                if unit in ("percent", "pp"):
                    self._classify_percentage(field_name, value, unit, context)
                elif unit == "multiplier":
                    self._classify_multiplier(field_name, value, context)
                elif unit in ("cents", "usd"):
                    self._classify_money(field_name, value, unit, context, raw_unit)
                elif self._match_count(value) and self._count_value_context(context, value):
                    self.non_amount.append({
                        "field": field_name, "value": str(value), "unit": unit or "none",
                        "context": context, "reason": "known count",
                    })
                elif self._action_param_context(context) and self._match_decimal(
                    self.action_percentages, value, tolerance=Decimal("0.11")
                ) is not None:
                    self.non_amount.append({
                        "field": field_name, "value": str(value), "unit": unit or "none",
                        "context": context, "reason": "action parameter",
                    })
                elif self._non_amount_context(context) or any(word in context for word in ("2026", "月", "日")):
                    self.non_amount.append({"field": field_name, "value": str(value), "unit": unit or "none", "context": context, "reason": "count/date/metric"})
                elif self._money_context(context):
                    self._classify_money(field_name, value, None, context, raw_unit)
                else:
                    if abs(value) >= 1000:
                        self.warnings.append({"type": "ambiguous_large_number", "value": str(value), "context": context, "detail": "大数字没有金额单位或业务上下文"})
                    else:
                        self.non_amount.append({"field": field_name, "value": str(value), "unit": unit or "none", "context": context, "reason": "no monetary context"})
        return {
            "passed": not self.violations,
            "checked": self.checked,
            "known_facts": self.known,
            "assumptions": self.assumptions,
            "non_amount": self.non_amount,
            "violations": self.violations,
            "warnings": self.warnings,
        }


def audit_numbers(payload: dict[str, Any], output: LLMAnalysisOutput) -> dict[str, Any]:
    audit = NumberAudit(payload)
    fields: list[tuple[str, str]] = [("summary", output.summary)]
    for index, cause in enumerate(output.root_causes):
        fields.extend([(f"root_causes[{index}].cause", cause.cause), (f"root_causes[{index}].reasoning", cause.reasoning)])
    for index, recommendation in enumerate(output.recommendations):
        fields.extend([(f"recommendations[{index}].action", recommendation.action), (f"recommendations[{index}].rationale", recommendation.rationale)])
    fields.append(("expected_impact.explanation", output.expected_impact.explanation))
    fields.extend((f"risks[{index}]", value) for index, value in enumerate(output.risks))
    fields.extend((f"missing_information[{index}]", value) for index, value in enumerate(output.missing_information))
    return audit.audit(fields)


def check_evidence(output: LLMAnalysisOutput, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(payload.get("allowed_evidence_ids") or [])
    referenced = set(output.evidence_references)
    for cause in output.root_causes:
        referenced.update(cause.evidence_ids)
    for recommendation in output.recommendations:
        referenced.update(recommendation.evidence_ids)
    unknown = sorted(referenced - allowed)
    primary_metric = (payload.get("primary_driver") or {}).get("metric")
    primary_id = f"ev-{primary_metric}" if primary_metric else None
    primary_hit = bool(primary_id) and any(primary_id in cause.evidence_ids for cause in output.root_causes)
    recommendation_primary_hit = bool(primary_id) and any(primary_id in recommendation.evidence_ids for recommendation in output.recommendations)
    return {
        "valid": not unknown,
        "allowed": sorted(allowed),
        "referenced": sorted(referenced),
        "unknown": unknown,
        "primary_evidence_id": primary_id,
        "primary_hit": primary_hit,
        "recommendation_primary_hit": recommendation_primary_hit,
        "all_causes_have_evidence": all(bool(cause.evidence_ids) for cause in output.root_causes),
        "all_recommendations_have_evidence": all(bool(recommendation.evidence_ids) for recommendation in output.recommendations),
    }


def check_action(output: LLMAnalysisOutput, payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("planned_simulation_action") or {}
    action_type = action.get("type")
    target_sku = action.get("sku")
    texts = [f"{item.action} {item.rationale}" for item in output.recommendations]
    target_texts = [text for text in texts if target_sku and target_sku in text]
    target_consistency = bool(target_sku) and bool(target_texts)
    warnings: list[dict[str, Any]] = []
    if action_type == "adjust_ad_budget":
        has_budget_down = any(BUDGET_DOWN_RE.search(text) for text in target_texts)
        has_budget_up = any(BUDGET_UP_RE.search(text) for text in target_texts)
        action_type_consistency = has_budget_down and not has_budget_up
        direction_ok = True
        if output.expected_impact.direction == "improve" and not any(word in output.expected_impact.explanation for word in CONDITIONAL_WORDS):
            warnings.append({"type": "unconditional_improvement", "detail": "direction=improve 但解释缺少条件/情景措辞"})
    elif action_type == "create_review_task":
        has_review = any(any(verb in text for verb in REVIEW_VERBS) for text in target_texts)
        has_budget_adjust = any(BUDGET_ADJUST_RE.search(text) for text in target_texts)
        action_type_consistency = has_review and not has_budget_adjust
        direction = output.expected_impact.direction
        explanation = output.expected_impact.explanation
        conditional = any(word in explanation for word in CONDITIONAL_WORDS)
        review_caveat = any(word in explanation for word in ("复盘", "复核", "核查", "审查", "审核", "排查", "不直接改变", "不直接", "模拟"))
        # A review task may describe a conditional repair path, but it must not
        # claim that the review itself already improves profit.
        direction_ok = direction in {"neutral", "uncertain"} or (direction == "improve" and conditional and review_caveat)
        if not direction_ok:
            warnings.append({"type": "impact_direction_conflict", "detail": "核查任务不应无条件承诺 direction=improve"})
    else:
        action_type_consistency = False
        direction_ok = False
    other_skus = [
        row.get("sku") for row in payload.get("sku_breakdown") or []
        if row.get("sku") and row.get("sku") != target_sku
    ]
    mentioned_other = sorted({sku for sku in other_skus if any(sku in text for text in target_texts)})
    if mentioned_other:
        warnings.append({"type": "multiple_skus_in_target_recommendation", "skus": mentioned_other, "detail": "目标建议中同时出现了其他 SKU"})
    primary_metric = (payload.get("primary_driver") or {}).get("metric")
    primary_id = f"ev-{primary_metric}" if primary_metric else None
    primary_causes = [cause for cause in output.root_causes if primary_id and primary_id in cause.evidence_ids]
    synonyms = PRIMARY_SYNONYMS.get(primary_metric or "", ())
    if primary_causes and synonyms and not any(
        any(word in f"{cause.cause} {cause.reasoning}" for word in synonyms) for cause in primary_causes
    ):
        warnings.append({"type": "cause_text_evidence_mismatch", "detail": "引用主要证据的根因文本没有出现该驱动因素的业务词"})
    causal = any(re.search(r"(导致|造成|引起)", f"{cause.cause} {cause.reasoning}") for cause in output.root_causes)
    caveat = any(
        word in f"{output.summary} {output.expected_impact.explanation} {' '.join(risk for risk in output.risks)}"
        for word in ("会计贡献", "不代表", "不能证明", "需验证", "可能", "待验证", "不是因果")
    )
    if causal and not caveat:
        warnings.append({"type": "causal_overclaim", "detail": "根因使用因果措辞但没有会计贡献/待验证说明"})
    if not output.risks:
        warnings.append({"type": "missing_risks", "detail": "没有给出风险"})
    if not output.missing_information:
        warnings.append({"type": "missing_information_empty", "detail": "没有给出待补充信息"})
    return {
        "target_sku": target_sku,
        "action_type": action_type,
        "target_consistency": target_consistency,
        "action_type_consistency": action_type_consistency,
        "impact_direction_consistency": direction_ok,
        "warnings": warnings,
    }


def check_forbidden_claims(output: LLMAnalysisOutput) -> dict[str, Any]:
    text = " ".join([
        output.summary,
        *(f"{cause.cause} {cause.reasoning}" for cause in output.root_causes),
        *(f"{item.action} {item.rationale}" for item in output.recommendations),
        output.expected_impact.explanation,
        *output.risks,
        *output.missing_information,
    ])
    violations = []
    for name, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 8):match.start()]
            negated = any(word in prefix for word in ("不", "未", "没有", "不会", "无需", "不要"))
            if name in {"guaranteed_profit", "real_execution_claim", "external_system_claim"} and negated:
                continue
            violations.append({"type": name, "text": match.group(0), "context": text[max(0, match.start() - 30):match.end() + 30]})
    return {"passed": not violations, "violations": violations}


def grade_run(case: dict[str, Any], oracle: OracleResult, run: dict[str, Any], expectation: RunExpectation) -> dict[str, Any]:
    status_match = run.get("status") == expectation.expected_status
    error = run.get("error") or {}
    reason = run.get("reason")
    error_code = error.get("code") or reason
    error_match = expectation.expected_error_code is None or error_code == expectation.expected_error_code
    reason_match = expectation.expected_reason is None or reason == expectation.expected_reason
    effects = run.get("side_effects") or {}
    no_side_effects = all(int(effects.get(key, 0)) == 0 for key in (
        "approval_calls", "execution_calls", "recommendation_calls", "business_external_calls", "database_writes",
    ))
    content_applicable = run.get("status") == "llm_success" and run.get("output") is not None
    hard_gates: dict[str, bool | None] = {
        "expected_status": status_match,
        "expected_error_code": error_match,
        "expected_reason": reason_match,
        "no_side_effects": no_side_effects,
    }
    details: dict[str, Any] = {
        "expected_status": expectation.expected_status,
        "actual_status": run.get("status"),
        "expected_error_code": expectation.expected_error_code,
        "actual_error_code": error_code,
        "expected_reason": expectation.expected_reason,
        "actual_reason": reason,
    }
    evidence: dict[str, Any] = {}
    action: dict[str, Any] = {}
    number: dict[str, Any] = {}
    forbidden: dict[str, Any] = {}
    semantic_warnings: list[dict[str, Any]] = []
    negative_control_detected: bool | None = None
    if not content_applicable and run.get("rejected_output"):
        try:
            rejected = LLMAnalysisOutput.model_validate(run["rejected_output"])
            evidence = check_evidence(rejected, run.get("payload") or {})
            details["rejected_evidence"] = evidence
        except ValidationError:
            pass
    if content_applicable:
        output = LLMAnalysisOutput.model_validate(run["output"])
        payload = run["payload"]
        evidence = check_evidence(output, payload)
        action = check_action(output, payload)
        number = audit_numbers(payload, output)
        forbidden = check_forbidden_claims(output)
        semantic_warnings = [*action.get("warnings", []), *number.get("warnings", [])]
        hard_gates.update({
            "schema_valid": True,
            "evidence_ids_valid": evidence["valid"],
            "primary_driver_hit": evidence["primary_hit"],
            "recommendation_primary_evidence": evidence["recommendation_primary_hit"],
            "action_target_consistency": action["target_consistency"],
            "action_type_consistency": action["action_type_consistency"],
            "impact_direction_consistency": action["impact_direction_consistency"],
            "amount_consistency": number["passed"],
            "forbidden_claims": forbidden["passed"],
        })
        details.update({
            "evidence": evidence,
            "action": action,
            "number_audit": number,
            "forbidden_claims": forbidden,
        })
        if expectation.expected_gate_failure:
            negative_control_detected = hard_gates.get(expectation.expected_gate_failure) is False
            passed = status_match and negative_control_detected
        else:
            passed = all(value is True for value in hard_gates.values())
    else:
        # Fallback / skipped paths have no content gates; they pass only when the
        # expected status and error/reason match and no side effect occurred.
        if expectation.negative_control:
            negative_control_detected = status_match and error_match and reason_match
        passed = status_match and error_match and reason_match and no_side_effects
        if expectation.expected_gate_failure:
            passed = False
    failure_reasons = [name for name, value in hard_gates.items() if value is False]
    if expectation.expected_gate_failure and negative_control_detected is False:
        failure_reasons.append(f"expected_grader_rejection:{expectation.expected_gate_failure}")
    result = {
        **run,
        "case_id": case.get("case_id"),
        "scenario": expectation.scenario,
        "expected": {
            "status": expectation.expected_status,
            "error_code": expectation.expected_error_code,
            "reason": expectation.expected_reason,
            "negative_control": expectation.negative_control,
            "expected_gate_failure": expectation.expected_gate_failure,
        },
        "hard_gates": hard_gates,
        "hard_gate_details": details,
        "evidence_consistency": evidence,
        "action_consistency": action,
        "number_check": number,
        "forbidden_claims": forbidden,
        "semantic_warnings": semantic_warnings,
        "failure_reasons": failure_reasons,
        "negative_control_detected": negative_control_detected,
        "passed": passed,
    }
    return result

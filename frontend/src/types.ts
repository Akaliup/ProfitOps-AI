export interface Period { start_date: string; end_date: string }
export interface Identity { id: string; name: string; role: 'analyst' | 'approver'; token: string }
export interface LLMConfig { configured: boolean; provider: string; model: string; fallback_policy: string }
export interface Demo {
  demo_mode: boolean; simulation: boolean; currency: string; identities: Identity[];
  stores: { id: string; name: string; marketplace: string }[]; data_range: Period | null;
  llm?: LLMConfig;
}
export interface Totals {
  sales: number; discounts: number; refunds: number; cogs: number; ad_spend: number;
  fulfillment: number; platform_fees: number; other_costs: number;
  orders: number; units: number; net_sales: number; net_profit: number; margin_pct: number | null;
}
export interface Driver {
  metric: string; label: string; previous: number; current: number; contribution: number; source: string;
}
export interface Evidence extends Driver {
  id: string; period: Period; previous_period: Period; store_id: string | null; currency: string; formula: string;
}
export interface Metrics {
  period: Period; previous_period: Period; store_id: string | null; currency: string;
  current: Totals; previous: Totals; changes: Record<string, { absolute: number; pct: number | null }>;
  anomaly: boolean; bridge_verified: boolean; source: string; drivers: Driver[];
  trend: (Totals & { date: string })[];
  sku_breakdown: { product_id: string; sku: string; name: string; current: Totals; previous: Totals; profit_delta: number; ad_spend_delta: number; inventory: number; inbound_inventory: number }[];
  data_quality: { complete: boolean; expected_records: number; observed_records: number; missing_records: number; incomplete_records: number; missing_examples: string[]; issues: string[] };
}
export interface Step {
  name: string; label: string; status: string; started_at: string; request_id: string;
  phase: string; duration_ms?: number; summary?: string;
}
export interface LLMUsage {
  prompt_tokens: number | null; completion_tokens: number | null;
  total_tokens: number | null; cached_tokens?: number | null;
}
export interface LLMRootCause { cause: string; reasoning: string; evidence_ids: string[] }
export interface LLMRecommendation {
  action: string; rationale: string; evidence_ids: string[];
  priority: 'high' | 'medium' | 'low';
}
export interface LLMExpectedImpact {
  direction: 'improve' | 'neutral' | 'uncertain'; horizon_days: number; explanation: string;
}
export interface LLMOutput {
  summary: string; root_causes: LLMRootCause[]; evidence_references: string[];
  recommendations: LLMRecommendation[]; expected_impact: LLMExpectedImpact;
  risks: string[]; missing_information: string[]; confidence: number;
}
export interface LLMRun {
  status: 'llm_success' | 'deterministic_fallback' | 'llm_failed' | 'not_requested';
  configured: boolean; provider: string; model: string; reason: string | null;
  attempts: number; latency_ms: number | null; usage: LLMUsage | null;
  output: LLMOutput | null; error: { code: string; message: string; retryable: boolean } | null;
}
export interface Analysis {
  id: string; status: string; request: { period: Period; store_id: string | null };
  steps: Step[]; result: { metrics: Metrics; findings: { conclusion: string; evidence: Evidence[]; primary_driver: Driver | null }; engine: string; llm?: LLMRun | null } | null;
  error: { code: string; message: string } | null; recommendation_id: string | null;
  request_id: string; created_at: string; replayed?: boolean;
}
export interface Execution {
  execution_id: string; recommendation_id: string; status: string; simulation: boolean; replayed?: boolean;
  result: { message: string; sku: string; before?: { daily_budget: number }; after?: { daily_budget: number }; external_calls: number; side_effect_count: number } | null;
  error: { code: string; message: string } | null; request_id?: string;
}
export interface Recommendation {
  id: string; analysis_id: string; status: string; title: string;
  body: {
    problem: string; period: Period; previous_period: Period; store_id: string | null; currency: string; engine: string;
    llm?: LLMRun | null;
    evidence: Evidence[]; primary_driver: Driver; recommendation: string;
    expected_impact: { kind: string; horizon_days: number; profit_improvement_min: number; profit_improvement_max: number; explanation: string };
    risks: string[]; missing_information: string[]; assumptions: string[]; source: string;
  };
  action: { type: string; simulation: boolean; sku: string; baseline_daily_budget: number; proposed_daily_budget: number };
  approved_by: string | null; approval_reason: string | null; created_at: string; execution: Execution | null;
}
export interface Audit {
  id: string; actor: string; action: string; object_type: string; object_id: string;
  request_id: string; outcome: string; details: Record<string, unknown>; created_at: string;
}
export interface Scope { days: number; storeId: string }

export interface Period { start_date: string; end_date: string }
export interface Identity { id: string; name: string; role: 'analyst' | 'approver'; token: string }
export interface LLMConfig { configured: boolean; provider: string; model: string; fallback_policy: string }
export interface Demo {
  demo_mode: boolean; simulation: boolean; currency: string; identities: Identity[];
  stores: { id: string; name: string; marketplace: string }[]; data_range: Period | null;
  llm?: LLMConfig;
  knowledge?: {
    documents: number; embedding_provider: string; embedding_model: string;
    seed_error?: { code: string; message: string } | null;
  };
}
export interface KnowledgeDocument {
  id: string; title: string; source: string; version: string; effective_date: string | null;
  active: boolean; chunk_count: number; created: boolean; reindexed?: boolean; created_at: string;
}
export interface KnowledgeDocumentInput {
  title: string; source: string; version: string; content: string; metadata?: Record<string, unknown>;
}
export interface KnowledgeHit {
  chunk_id: string; document_id: string; title: string; source: string; version: string;
  heading: string | null; content: string; score: number; rank: number;
  // 排序分与拒答置信度是两套量：score 只表示融合排名，confidence 用于 no_answer
  confidence?: number | null;
}
export interface KnowledgeCitation extends KnowledgeHit { cited: boolean }
export interface KnowledgeSearch {
  query: string; hits: KnowledgeHit[]; no_answer: boolean; embedding_model: string; retrieval_ms: number;
  retrieval_mode?: string; rankers?: string[];
}
export interface KnowledgeContext {
  query: string; hits: KnowledgeCitation[]; no_answer: boolean; embedding_model: string; retrieval_ms: number;
}
export interface ImportIssue { row: number; field: string | null; code: string; message: string }
export interface ImportCheck { code: string; ok: boolean; detail: string }
export interface ImportTotals {
  rows: number; sales: number; discounts: number; refunds: number; net_sales: number;
  cogs: number; ad_spend: number; fulfillment: number; platform_fees: number;
  other_costs: number; net_profit: number; inventory: number; inbound_inventory: number;
}
export interface ImportSummary {
  id: string | null; kind: 'orders' | 'operations' | 'ads' | 'inventory'; filename: string;
  status: 'ready' | 'blocked' | 'committed' | 'noop'; can_commit: boolean; replayed: boolean;
  content_sha256: string; total_rows: number; accepted_rows: number; skipped_rows: number;
  rejected_rows: number; duplicate_rows: number; totals: ImportTotals;
  issues: ImportIssue[]; issue_counts: Record<string, number>; checks: ImportCheck[];
  preview_rows?: Record<string, string>[];
  created_by: string | null; created_at: string | null;
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
  knowledge_references?: string[];
  recommendations: LLMRecommendation[]; expected_impact: LLMExpectedImpact;
  risks: string[]; missing_information: string[]; confidence: number;
}
export interface LLMToolCallTrace {
  round: number; tool_call_id: string; name: string; arguments: Record<string, unknown>;
  ok: boolean; result_summary: string; error_code: string | null; latency_ms: number;
}
export interface LLMToolRound {
  round: number; finish_reason: string | null; tool_call_count: number;
  latency_ms: number; usage: LLMUsage | null;
}
export interface LLMRun {
  status: 'llm_success' | 'deterministic_fallback' | 'llm_failed' | 'not_requested';
  configured: boolean; provider: string; model: string; reason: string | null;
  attempts: number; latency_ms: number | null; usage: LLMUsage | null;
  output: LLMOutput | null; error: { code: string; message: string; retryable: boolean } | null;
  native_tools?: boolean; termination_reason?: string | null;
  rounds?: LLMToolRound[]; tool_calls?: LLMToolCallTrace[];
}
export interface Analysis {
  id: string; status: string; request: { period: Period; store_id: string | null };
  steps: Step[]; result: { metrics: Metrics; findings: { conclusion: string; evidence: Evidence[]; primary_driver: Driver | null }; engine: string; llm?: LLMRun | null; knowledge?: KnowledgeSearch | null; knowledge_error?: { code: string; message: string } | null } | null;
  error: { code: string; message: string } | null; recommendation_id: string | null;
  attempts: number; max_attempts: number; next_run_at: string | null;
  request_id: string; created_at: string; replayed?: boolean;
}
export interface Execution {
  execution_id: string; recommendation_id: string; status: string; simulation: boolean; replayed?: boolean;
  result: { message: string; sku: string; before?: { daily_budget: number }; after?: { daily_budget: number }; external_calls: number; side_effect_count: number } | null;
  error: { code: string; message: string } | null; request_id?: string;
}
export interface ApprovalSnapshot {
  version: number; snapshot_type: string; captured_at: string;
  scope: { period: Period; store_id: string | null };
  ledger_hash: string; action_hash: string;
  action: Record<string, unknown>;
  current_totals: { net_sales: number; net_profit: number; margin_pct: number | null };
  previous_totals: { net_sales: number; net_profit: number; margin_pct: number | null };
  data_quality: { complete: boolean; missing_records: number; incomplete_records: number };
  sku_breakdown: Array<Record<string, unknown>>;
}
export interface Recommendation {
  id: string; analysis_id: string; status: string; title: string;
  body: {
    problem: string; period: Period; previous_period: Period; store_id: string | null; currency: string; engine: string;
    llm?: LLMRun | null;
    knowledge?: KnowledgeContext | null;
    evidence: Evidence[]; primary_driver: Driver; recommendation: string;
    expected_impact: { kind: string; horizon_days: number; profit_improvement_min: number; profit_improvement_max: number; explanation: string };
    risks: string[]; missing_information: string[]; assumptions: string[]; source: string;
    approval?: ApprovalSnapshot | null;
  };
  action: { type: string; simulation: boolean; sku: string; baseline_daily_budget: number; proposed_daily_budget: number };
  approved_by: string | null; approval_reason: string | null; created_at: string; execution: Execution | null;
}
export interface Audit {
  id: string; actor: string; action: string; object_type: string; object_id: string;
  request_id: string; outcome: string; details: Record<string, unknown>; created_at: string;
}
export interface Scope { days: number; storeId: string }
export type JobStatus =
  | 'queued' | 'running' | 'waiting_approval' | 'completed' | 'insufficient_data' | 'failed'
  | 'approved' | 'rejected' | 'executed' | 'execution_failed';

export interface OpsSummary {
  tenant_id: string; generated_at: string;
  window: { hours: number; start: string; end: string; timezone: string };
  source: { kind: string; tables: string[]; note: string };
  jobs: {
    total: number; by_status: Record<JobStatus, number>; retry_count: number;
    retry_scheduled_events: number; dead_letter: number; dead_letter_events: number;
    error_codes: { code: string; count: number }[];
  };
  llm: {
    runs: number; model_calls: number; provider_requests: number; provider_retries: number;
    no_model_call_runs: number;
    by_status: Record<string, number>; native_tool_runs: number;
    termination_reasons: { reason: string; count: number }[];
  };
  tools: {
    calls: number; failed_calls: number; rounds: number;
    by_name: { name: string; calls: number; failed: number }[];
  };
  tokens: {
    runs_with_usage: number; runs_without_usage: number;
    prompt: number | null; completion: number | null; total: number | null; cached: number | null;
  };
  cost: {
    status: 'complete' | 'partial' | 'no_data'; currency: string; amount_usd: number | null;
    known_subtotal_usd: number | null;
    priced_runs: number; unpriced_runs: number;
    priced_calls: number; unpriced_calls: number;
    pricing_version: string | null;
    source: string; note: string;
  };
  approvals: { current_state: boolean; by_status: Record<string, number> };
  executions: { current_state: boolean; total: number; by_status: Record<string, number> };
  recent_failures: {
    id: string; status: string; request_id: string; attempts: number; max_attempts: number;
    dead_letter: boolean; error_code: string | null; error_message: string | null;
    updated_at: string; created_at: string;
  }[];
  pricing: { version: string; source: string };
  notes: string[];
}

export interface InventoryRow {
  store_id: string; sku: string; on_hand: number; reserved: number; available: number;
  daily_sales: string; coverage_days: string | null; lead_time_days: number;
  risk: string; reorder_quantity: number | null; timely_inbound: number;
  evidence_id: string; import_batch_id: string; status: string;
}
export interface WeeklyIntelligence {
  id: string; period: Period; metrics: Metrics; findings: { conclusion: string };
  stores: {store_id: string; name: string; current: Totals; profit_delta: number; risk_skus: number}[];
  inventory: {items: InventoryRow[]; coverage_note: string};
  sku_diagnostics: {store_id: string; sku: string; profit_delta: number; ad_spend_delta: number; refund_delta: number; ad_growth_alert: boolean}[];
  analysis: {status: string; reason?: string; claims: {explanation: string; evidence_ids: string[]}[]};
  evidence: {id: string; [key: string]: unknown}[];
  source_rows: {id: string; batch_id: string; row: number; kind: string}[];
  limitations: string[];
}

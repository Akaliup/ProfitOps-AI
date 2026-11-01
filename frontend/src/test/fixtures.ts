import type { Analysis, Demo, KnowledgeContext, KnowledgeDocument, KnowledgeSearch, LLMRun, Metrics, Recommendation } from '../types'

export const demo: Demo = {
  demo_mode: true, simulation: true, currency: 'USD',
  identities: [{ id: 'demo-analyst', name: '经营分析师', role: 'analyst', token: 'demo-analyst' }, { id: 'demo-approver', name: '审批负责人', role: 'approver', token: 'demo-approver' }],
  stores: [{ id: 'us-store', name: '北美旗舰店', marketplace: 'Amazon US' }],
  data_range: { start_date: '2026-09-01', end_date: '2026-10-30' },
  llm: { configured: false, provider: 'deepseek', model: 'deepseek-flash', fallback_policy: 'rules' },
  knowledge: { documents: 1, embedding_provider: 'hashing', embedding_model: 'hashing-v1' },
}
export const knowledgeSearch: KnowledgeSearch = {
  query: '广告预算下调审批', no_answer: false, embedding_model: 'hashing-v1', retrieval_ms: 8.2,
  hits: [{
    chunk_id: 'chunk-ad-1', document_id: 'doc-ad', title: '广告预算调整与审批规则',
    source: 'synthetic://profitops/knowledge/ad-budget-policy', version: 'v1', heading: '审批流程',
    content: '分析师发起建议；审批负责人复核后批准；只有批准后的建议可以模拟执行。',
    score: 0.42, rank: 1,
  }],
}
export const knowledgeDocument: KnowledgeDocument = {
  id: 'doc-ad', title: '广告预算调整与审批规则', source: 'synthetic://profitops/knowledge/ad-budget-policy',
  version: 'v1', effective_date: '2026-10-01', active: true, chunk_count: 3, created: false,
  created_at: '2026-10-01T00:00:00Z',
}
const knowledgeContext: KnowledgeContext = {
  ...knowledgeSearch,
  hits: knowledgeSearch.hits.map(hit => ({ ...hit, cited: true })),
}
export const llmFallback: LLMRun = {
  status: 'deterministic_fallback', configured: false, provider: 'deepseek', model: 'deepseek-flash',
  reason: 'not_configured', attempts: 0, latency_ms: null, usage: null, output: null, error: null,
}
export const llmSuccess: LLMRun = {
  status: 'llm_success', configured: true, provider: 'deepseek', model: 'deepseek-flash',
  reason: null, attempts: 1, latency_ms: 321.5,
  usage: { prompt_tokens: 120, completion_tokens: 80, total_tokens: 200 },
  output: {
    summary: '广告支出增长快于销售额，吞噬了增长带来的毛利。',
    root_causes: [{ cause: '广告支出增长快于销售额', reasoning: '广告支出的负向贡献绝对值最大。', evidence_ids: ['ev-ad_spend'] }],
    evidence_references: ['ev-ad_spend'], knowledge_references: ['chunk-ad-1'],
    recommendations: [{ action: '对 TRAVEL-BAG-01 的日广告预算做 15% 受控模拟下调', rationale: '先模拟验证节省空间。', evidence_ids: ['ev-ad_spend'], priority: 'high' }],
    expected_impact: { direction: 'improve', horizon_days: 30, explanation: '若流量与转化不变，广告支出下降会改善利润。' },
    risks: ['预算下调可能减少流量。'], missing_information: ['活动级 ROAS'], confidence: 0.72,
  },
  error: null,
}
export const llmNativeSuccess: LLMRun = {
  ...llmSuccess, latency_ms: 1234.5,
  usage: { prompt_tokens: 3400, completion_tokens: 500, total_tokens: 3900 },
  native_tools: true, termination_reason: 'final_answer',
  rounds: [
    { round: 1, finish_reason: 'tool_calls', tool_call_count: 1, latency_ms: 400.2, usage: { prompt_tokens: 1000, completion_tokens: 80, total_tokens: 1080 } },
    { round: 2, finish_reason: 'tool_calls', tool_call_count: 1, latency_ms: 401.3, usage: { prompt_tokens: 1200, completion_tokens: 120, total_tokens: 1320 } },
    { round: 3, finish_reason: 'stop', tool_call_count: 0, latency_ms: 433, usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } },
  ],
  tool_calls: [
    { round: 1, tool_call_id: 'call-profit', name: 'get_profit_analysis', arguments: { period: { start_date: '2026-10-01', end_date: '2026-10-30' }, store_id: null }, ok: true, result_summary: 'ok; net_profit=-26000; anomaly=True; evidence=8', error_code: null, latency_ms: 42.1 },
    { round: 2, tool_call_id: 'call-sku', name: 'get_sku_breakdown', arguments: { period: { start_date: '2026-10-01', end_date: '2026-10-30' }, store_id: null }, ok: true, result_summary: 'ok; rows=3', error_code: null, latency_ms: 38.4 },
  ],
}
export const llmNativeFailure: LLMRun = {
  status: 'deterministic_fallback', configured: true, provider: 'deepseek', model: 'deepseek-flash',
  reason: 'llm_tool_execution_failed', attempts: 2, latency_ms: 800.5,
  usage: { prompt_tokens: 2200, completion_tokens: 300, total_tokens: 2500 },
  output: null, error: { code: 'llm_tool_execution_failed', message: '工具执行失败：模拟账本不可用', retryable: false },
  native_tools: true, termination_reason: 'llm_tool_execution_failed',
  rounds: [
    { round: 1, finish_reason: 'tool_calls', tool_call_count: 1, latency_ms: 390.5, usage: { prompt_tokens: 1000, completion_tokens: 100, total_tokens: 1100 } },
    { round: 2, finish_reason: 'tool_calls', tool_call_count: 1, latency_ms: 410, usage: { prompt_tokens: 1200, completion_tokens: 200, total_tokens: 1400 } },
  ],
  tool_calls: [
    { round: 1, tool_call_id: 'call-profit', name: 'get_profit_analysis', arguments: { period: { start_date: '2026-10-01', end_date: '2026-10-30' }, store_id: null }, ok: true, result_summary: 'ok; net_profit=-26000; anomaly=True; evidence=8', error_code: null, latency_ms: 41.2 },
    { round: 2, tool_call_id: 'call-sku', name: 'get_sku_breakdown', arguments: { period: { start_date: '2026-10-01', end_date: '2026-10-30' }, store_id: null }, ok: false, result_summary: '工具调用失败', error_code: 'llm_tool_execution_failed', latency_ms: 37.9 },
  ],
}
const totals = { sales: 120000, discounts: 0, refunds: 0, cogs: 36000, ad_spend: 45000, fulfillment: 14000, platform_fees: 6000, other_costs: 0, orders: 12, units: 12, net_sales: 120000, net_profit: 19000, margin_pct: 15.83 }
const prior = { ...totals, sales: 100000, cogs: 30000, ad_spend: 10000, fulfillment: 10000, platform_fees: 5000, net_sales: 100000, net_profit: 45000, margin_pct: 45 }
export const metrics: Metrics = {
  period: { start_date: '2026-10-01', end_date: '2026-10-30' }, previous_period: { start_date: '2026-09-01', end_date: '2026-09-30' },
  store_id: null, currency: 'USD', current: totals, previous: prior,
  changes: { sales: { absolute: 20000, pct: 20 }, net_profit: { absolute: -26000, pct: -57.78 }, margin_pct: { absolute: -29.17, pct: null }, ad_spend: { absolute: 35000, pct: 350 } },
  anomaly: true, bridge_verified: true, source: 'test fixture',
  drivers: [{ metric: 'ad_spend', label: '广告支出', previous: 10000, current: 45000, contribution: -35000, source: 'daily_operations.ad_spend' }],
  trend: [{ ...totals, date: '2026-10-01' }],
  sku_breakdown: [{ product_id: 'bag', sku: 'TRAVEL-BAG-01', name: '轻量旅行背包', current: totals, previous: prior, profit_delta: -26000, ad_spend_delta: 35000, inventory: 100, inbound_inventory: 40 }],
  data_quality: { complete: true, expected_records: 60, observed_records: 60, missing_records: 0, incomplete_records: 0, missing_examples: [], issues: [] },
}
export const analysis: Analysis = {
  id: 'analysis-1', status: 'waiting_approval', request: { period: metrics.period, store_id: null },
  steps: [{ name: 'get_sales_metrics', label: '读取经营数据', status: 'completed', started_at: '2026-10-30T00:00:00Z', request_id: 'test-request', phase: 'analysis', duration_ms: 10, summary: '读取 60 条每日经营记录' }],
  result: { metrics, findings: { conclusion: '销售额上涨、净利润下降。广告支出是最大负向贡献项。', evidence: [], primary_driver: metrics.drivers[0] }, engine: 'deterministic-rules-v1', llm: llmFallback, knowledge: knowledgeSearch },
  error: null, recommendation_id: 'rec-1', attempts: 1, max_attempts: 3, next_run_at: null,
  request_id: 'test-request', created_at: '2026-10-30T00:00:00Z',
}
export const recommendation: Recommendation = {
  id: 'rec-1', analysis_id: 'analysis-1', status: 'pending_approval', title: 'TRAVEL-BAG-01 · 广告预算优化模拟',
  body: {
    problem: '销售额上涨、净利润下降', period: metrics.period, previous_period: metrics.previous_period, store_id: null, currency: 'USD', engine: 'deterministic-rules-v1', llm: llmFallback, knowledge: knowledgeContext,
    evidence: [{ ...metrics.drivers[0], id: 'ev-ad_spend', period: metrics.period, previous_period: metrics.previous_period, store_id: null, currency: 'USD', formula: '-(current - previous)' }],
    primary_driver: metrics.drivers[0], recommendation: '广告预算下调 15%，先模拟后复核。',
    expected_impact: { kind: 'conditional_scenario', horizon_days: 30, profit_improvement_min: 3000, profit_improvement_max: 6000, explanation: '收入不变的条件情景，非保证收益。' },
    risks: ['可能减少流量'], missing_information: ['转化弹性'], assumptions: ['未来支出等于历史均值'], source: 'test fixture',
  },
  action: { type: 'adjust_ad_budget', simulation: true, sku: 'TRAVEL-BAG-01', baseline_daily_budget: 1000, proposed_daily_budget: 850 },
  approved_by: null, approval_reason: null, created_at: '2026-10-30T00:00:00Z', execution: null,
}

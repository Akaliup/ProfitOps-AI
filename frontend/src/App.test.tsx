import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { analysis, demo, knowledgeDocument, knowledgeSearch, llmNativeFailure, llmNativeSuccess, llmSuccess, metrics, recommendation } from './test/fixtures'
import type { Analysis, OpsSummary, Recommendation } from './types'

vi.mock('./components/TrendChart', () => ({ TrendChart: () => <div>经营趋势图</div> }))
let rows: Recommendation[]
let history: Analysis[]
let executeCount: number
let lastActor: string
let apiFailure: boolean
let empty: boolean
let documents = [structuredClone(knowledgeDocument)]
let noKnowledgeAnswer: boolean

const opsSummary: OpsSummary = {
  tenant_id: 'demo', generated_at: '2026-11-02T00:00:00Z',
  window: { hours: 24, start: '2026-11-01T00:00:00Z', end: '2026-11-02T00:00:00Z', timezone: 'UTC' },
  source: { kind: 'database', tables: ['analyses'], note: '数据来自持久化记录。' },
  jobs: {
    total: 4, by_status: { queued: 1, running: 0, waiting_approval: 0, completed: 1, insufficient_data: 0, failed: 2 },
    retry_count: 3, retry_scheduled_events: 1, dead_letter: 1, dead_letter_events: 1,
    error_codes: [{ code: 'llm_network_error', count: 2 }],
  },
  llm: {
    runs: 2, by_status: { llm_success: 1, deterministic_fallback: 1, llm_failed: 0, not_requested: 0 },
    native_tool_runs: 1, termination_reasons: [{ reason: 'final_answer', count: 1 }],
  },
  tools: { calls: 2, failed_calls: 1, rounds: 2, by_name: [{ name: 'get_profit_analysis', calls: 2, failed: 1 }] },
  tokens: { runs_with_usage: 1, runs_without_usage: 1, prompt: 1000, completion: 100, total: 1100, cached: 200 },
  cost: {
    status: 'partial', currency: 'USD', amount_usd: null, priced_runs: 1, unpriced_runs: 1,
    pricing_version: '2026-11-02', source: 'https://api-docs.deepseek.com/quick_start/pricing', note: '估算',
  },
  approvals: { current_state: true, by_status: { pending_approval: 1, approved: 0, rejected: 0, executed: 0, execution_failed: 0 } },
  executions: { current_state: true, total: 0, by_status: {} },
  recent_failures: [{
    id: 'analysis-failed-1', status: 'failed', request_id: 'req-failed', attempts: 3, max_attempts: 3,
    dead_letter: true, error_code: 'llm_network_error', error_message: 'upstream reset',
    updated_at: '2026-11-02T00:00:00Z', created_at: '2026-11-01T00:00:00Z',
  }],
  pricing: { version: '2026-11-02', source: 'https://api-docs.deepseek.com/quick_start/pricing' },
  notes: ['工具参数与原始提示词不返回。'],
}

beforeEach(() => {
  rows = []; history = []; executeCount = 0; lastActor = ''; apiFailure = false; empty = false
  documents = [structuredClone(knowledgeDocument)]; noKnowledgeAnswer = false
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    const headers = new Headers(options?.headers)
    const actor = headers.get('Authorization') ?? ''
    const respond = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
    if (path === '/api/demo') return respond(empty ? { ...demo, data_range: null } : demo)
    if (path.startsWith('/api/dashboard/summary')) return apiFailure ? respond({ error: { code: 'source_failed', message: '测试数据源故障' }, request_id: 'visible-request-id' }, 503) : respond(metrics)
    if (path === '/api/analyses' && options?.method === 'POST') {
      rows = [structuredClone(recommendation)]; history = [structuredClone(analysis)]
      return respond({ ...analysis, status: 'queued', steps: [], result: null, recommendation_id: null }, 202)
    }
    if (path === '/api/analyses') return respond({ items: history })
    if (path === '/api/analyses/analysis-1') return respond(analysis)
    if (path === '/api/analyses/analysis-failed-1') return respond({ ...analysis, id: 'analysis-failed-1' })
    if (path.startsWith('/api/ops/summary')) return respond(opsSummary)
    if (path === '/api/recommendations') return respond({ items: rows })
    if (path === '/api/knowledge/documents' && options?.method === 'POST') {
      const body = JSON.parse(String(options.body))
      const item = { ...structuredClone(knowledgeDocument), id: `doc-${documents.length + 1}`, title: body.title, source: body.source, version: body.version, created: true, chunk_count: 1 }
      documents = [item, ...documents]
      return respond(item, 201)
    }
    if (path === '/api/knowledge/documents') return respond({ items: documents })
    if (path.startsWith('/api/knowledge/documents/') && options?.method === 'DELETE') {
      const id = path.split('/').pop()!
      documents = documents.map(doc => doc.id === id ? { ...doc, active: false } : doc)
      return respond({ ...documents.find(doc => doc.id === id)!, active: false })
    }
    if (path === '/api/knowledge/search') {
      return respond(noKnowledgeAnswer ? { ...knowledgeSearch, hits: [], no_answer: true } : knowledgeSearch)
    }
    if (path.startsWith('/api/audit-logs')) return respond({ items: [] })
    if (path.endsWith('/approve') || path.endsWith('/reject')) {
      lastActor = actor
      rows = [{ ...rows[0], status: path.endsWith('/approve') ? 'approved' : 'rejected', approval_reason: JSON.parse(String(options?.body)).reason }]
      return respond(rows[0])
    }
    if (path.endsWith('/execute')) {
      lastActor = actor
      executeCount++
      const result = { execution_id: 'exec-1', recommendation_id: 'rec-1', status: 'executed', simulation: true, replayed: executeCount > 1, error: null, result: { message: '模拟执行完成', sku: 'TRAVEL-BAG-01', before: { daily_budget: 1000 }, after: { daily_budget: 850 }, external_calls: 0, side_effect_count: 1 } }
      rows = [{ ...rows[0], status: 'executed', execution: result }]
      return respond(result)
    }
    if (path === '/api/demo/seed') { empty = false; return respond({ seeded: true, message: '初始化成功' }) }
    throw new Error(`Unexpected request: ${path}`)
  }))
})

describe('ProfitOps workspace', () => {
  it('renders real API metrics and updates scope', async () => {
    render(<App />)
    expect(await screen.findByText('销售在增长，利润却没有跟上。')).toBeVisible()
    expect(screen.getByText('轻量旅行背包')).toBeVisible()
    fireEvent.change(screen.getByLabelText('统计周期'), { target: { value: '7' } })
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('start_date=2026-10-24'))).toBe(true))
    fireEvent.change(screen.getByLabelText('店铺'), { target: { value: 'us-store' } })
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('store_id=us-store'))).toBe(true))
  })

  it('starts analysis, polls, approves and replays a simulated execution', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: '启动 Profit Agent' }))
    expect(await screen.findByText('读取经营数据')).toBeVisible()
    await user.click(await screen.findByRole('button', { name: '查看经营建议' }))
    expect(await screen.findByText(/当前为经营分析师/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '批准建议' })).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('演示身份'), 'demo-approver')
    await user.click(await screen.findByRole('button', { name: '批准建议' }))
    expect(await screen.findByRole('button', { name: '模拟执行已批准动作' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '模拟执行已批准动作' }))
    expect(await screen.findByRole('heading', { name: /模拟执行结果/ })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '再次请求 · 验证幂等' }))
    expect(await screen.findByText(/重复请求已命中原执行记录/)).toBeVisible()
    expect(executeCount).toBe(2)
    expect(lastActor).toBe('Bearer demo-approver')
  })

  it('rejects with the entered reason', async () => {
    rows = [structuredClone(recommendation)]; history = [structuredClone(analysis)]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /审批与执行/ }))
    await user.selectOptions(screen.getByLabelText('演示身份'), 'demo-approver')
    const reason = await screen.findByLabelText('审批意见')
    await user.clear(reason); await user.type(reason, '等待补充广告转化证据')
    await user.click(screen.getByRole('button', { name: '拒绝建议' }))
    expect(await screen.findByText('审批意见：等待补充广告转化证据')).toBeVisible()
    expect(rows[0].status).toBe('rejected')
  })

  it('previews the simulated action before approval and filters audit by request id', async () => {
    rows = [structuredClone(recommendation)]; history = [structuredClone(analysis)]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /审批与执行/ }))
    expect(await screen.findByRole('heading', { name: /待执行的模拟动作/ })).toBeVisible()
    expect(screen.getAllByText('US$10.00').length).toBeGreaterThan(0)
    expect(screen.getAllByText('US$8.50').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: /审计日志/ }))
    await user.type(screen.getByLabelText('按请求 ID 过滤'), 'req-123')
    await user.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('request_id=req-123'))).toBe(true))
  })

  it('shows real LLM root causes, evidence references, latency and tokens', async () => {
    history = [structuredClone({ ...analysis, result: { ...analysis.result!, llm: llmSuccess } })]
    rows = [structuredClone(recommendation)]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 工作台/ }))
    expect((await screen.findAllByText('真实调用成功')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('321.5 ms').length).toBeGreaterThan(0)
    expect(screen.getAllByText('输入 120 / 输出 80').length).toBeGreaterThan(0)
    expect(screen.getAllByText('广告支出增长快于销售额').length).toBeGreaterThan(0)
    expect(screen.getAllByText('ev-ad_spend').length).toBeGreaterThan(0)
    expect(screen.getAllByText('置信度 72%').length).toBeGreaterThan(0)
  })

  it('labels deterministic fallback instead of pretending it is LLM output', async () => {
    history = [structuredClone(analysis)]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 工作台/ }))
    expect((await screen.findAllByText('已降级为确定性规则')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/未配置 API Key/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/不是 LLM 输出/).length).toBeGreaterThan(0)
    expect(screen.getByText('本次分析走单次 JSON 路径，未产生原生工具轮次。')).toBeVisible()
  })

  it('renders native rounds, tool arguments, per-round tokens and termination', async () => {
    history = [structuredClone({ ...analysis, result: { ...analysis.result!, llm: llmNativeSuccess } })]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 工作台/ }))
    expect(await screen.findByRole('heading', { name: /Agent 执行轨迹/ })).toBeVisible()
    const trace = screen.getByRole('region', { name: 'Agent 执行轨迹' })
    expect(within(trace).getByText('ROUND 1')).toBeVisible()
    expect(within(trace).getByText('ROUND 3')).toBeVisible()
    expect(within(trace).getAllByText('get_profit_analysis').length).toBeGreaterThan(0)
    expect(within(trace).getAllByText('get_sku_breakdown').length).toBeGreaterThan(0)
    expect(within(trace).getAllByText('✓ 成功').length).toBe(2)
    expect(within(trace).getByText('模型给出最终答案')).toBeVisible()
    expect(within(trace).getByText('3900')).toBeVisible()
    expect(within(trace).getByText('1080 tokens')).toBeVisible()
    expect(within(trace).getByText('0 次工具')).toBeVisible()
    expect(within(trace).getAllByText('2026-10-01 → 2026-10-30 · 全部店铺').length).toBe(2)
  })

  it('never renders non-whitelisted or malformed tool argument values', async () => {
    const sensitive = structuredClone(llmNativeSuccess)
    sensitive.tool_calls = [
      {
        ...sensitive.tool_calls![0],
        arguments: {
          period: { start_date: '2026-10-01', end_date: '2026-10-30' },
          store_id: 'us-store',
          api_key: 'sk-live-secret',
          Authorization: 'Bearer top-secret',
          token: 'tok-secret',
          password: 'pwd-secret',
        },
      },
      {
        ...sensitive.tool_calls![1],
        arguments: {
          api_key: 'sk-only-secret',
          Authorization: 'Bearer only-secret',
          token: 'tok-only-secret',
          password: 'pwd-only-secret',
        },
      },
      {
        ...sensitive.tool_calls![1],
        tool_call_id: 'call-malformed',
        round: 3,
        arguments: {
          period: { start_date: 'sk-period-secret', end_date: 'Bearer period-secret' },
          store_id: 'token=store-secret',
          password: 'pwd-malformed',
        },
      },
    ]
    sensitive.rounds![2] = { ...sensitive.rounds![2], tool_call_count: 1 }
    history = [structuredClone({ ...analysis, result: { ...analysis.result!, llm: sensitive } })]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 工作台/ }))
    const trace = await screen.findByRole('region', { name: 'Agent 执行轨迹' })
    expect(within(trace).getByText('2026-10-01 → 2026-10-30 · 店铺 us-store · 4 个其他字段')).toBeVisible()
    expect(within(trace).getByText('4 个其他字段')).toBeVisible()
    expect(within(trace).getByText('period 已隐藏 · store_id 已隐藏 · 1 个其他字段')).toBeVisible()
    expect(within(trace).getByText('ROUND 3')).toBeVisible()
    for (const secret of [
      'sk-live-secret', 'top-secret', 'tok-secret', 'pwd-secret',
      'sk-only-secret', 'only-secret', 'tok-only-secret', 'pwd-only-secret',
      'sk-period-secret', 'period-secret', 'token=store-secret', 'pwd-malformed',
    ]) {
      expect(document.body.textContent).not.toContain(secret)
    }
  })

  it('marks failed tool calls as failed and keeps the error visible', async () => {
    history = [structuredClone({ ...analysis, result: { ...analysis.result!, llm: llmNativeFailure } })]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 工作台/ }))
    expect(await screen.findByRole('heading', { name: /Agent 执行轨迹/ })).toBeVisible()
    const trace = screen.getByRole('region', { name: 'Agent 执行轨迹' })
    expect(within(trace).getAllByText('✓ 成功').length).toBe(1)
    expect(within(trace).getAllByText('× 失败').length).toBe(1)
    expect(within(trace).getByText('错误码：llm_tool_execution_failed')).toBeVisible()
    expect(within(trace).getByText('工具执行失败')).toBeVisible()
  })

  it('shows API error and correlation id', async () => {
    apiFailure = true
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('测试数据源故障 · 请求 ID visible-request-id')
  })

  it('initializes an empty database through the API', async () => {
    empty = true
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: '初始化演示数据' }))
    expect(await screen.findByText('销售在增长，利润却没有跟上。')).toBeVisible()
    expect(await screen.findByRole('status')).toHaveTextContent('初始化成功')
  })

  it('searches the knowledge base and distinguishes no-answer results', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /运营知识库/ }))
    expect(await screen.findByRole('heading', { name: /运营知识库/, level: 2 })).toBeVisible()
    expect((await screen.findAllByText('广告预算调整与审批规则')).length).toBeGreaterThan(0)
    await user.type(screen.getByLabelText('知识库检索'), '广告预算下调审批')
    await user.click(screen.getByRole('button', { name: '检索知识' }))
    expect(await screen.findByText('审批流程')).toBeVisible()
    expect(screen.getAllByText('chunk-ad-1').length).toBeGreaterThan(0)
    noKnowledgeAnswer = true
    await user.clear(screen.getByLabelText('知识库检索'))
    await user.type(screen.getByLabelText('知识库检索'), '如何预订会议室午餐')
    await user.click(screen.getByRole('button', { name: '检索知识' }))
    expect(await screen.findByText('无可靠答案')).toBeVisible()
  })

  it('lets an approver add a knowledge document', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /运营知识库/ }))
    await user.selectOptions(screen.getByLabelText('演示身份'), 'demo-approver')
    await user.type(screen.getByLabelText('知识标题'), '退款升级规则')
    await user.type(screen.getByLabelText('知识正文'), '# 退款升级\n\n退款率连续 7 天高于基线时升级为运营专项。')
    await user.click(screen.getByRole('button', { name: '添加知识文档' }))
    expect(await screen.findByText(/知识文档已创建/)).toBeVisible()
    expect(await screen.findByText('退款升级规则')).toBeVisible()
  })

  it('shows retrieved knowledge and LLM citation status in recommendations', async () => {
    rows = [structuredClone(recommendation)]; history = [structuredClone(analysis)]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /审批与执行/ }))
    expect(await screen.findByRole('heading', { name: /知识库参考/ })).toBeVisible()
    expect(screen.getByText('✓ LLM 已引用')).toBeVisible()
    expect(screen.getAllByText('chunk-ad-1').length).toBeGreaterThan(0)
  })

  it('shows knowledge references in the LLM panel', async () => {
    history = [structuredClone({ ...analysis, result: { ...analysis.result!, llm: llmSuccess } })]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 工作台/ }))
    expect(await screen.findByText('知识引用')).toBeVisible()
    expect(screen.getAllByText('chunk-ad-1').length).toBeGreaterThan(0)
  })

  it('gates the ops page by role, shows unknown cost and opens a failed trace', async () => {
    history = [structuredClone(analysis)]
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('销售在增长，利润却没有跟上。')
    await user.click(screen.getByRole('button', { name: /Agent 运维/ }))
    expect(await screen.findByText('需要审批负责人权限')).toBeVisible()

    await user.selectOptions(screen.getByLabelText('演示身份'), 'demo-approver')
    expect((await screen.findAllByText('部分未知')).length).toBeGreaterThan(0)
    expect(screen.getByText('1 次调用缺少 Usage 或价格，未按 0 计。')).toBeVisible()
    expect(screen.getAllByText('死信').length).toBeGreaterThan(0)
    expect(screen.getByText(/upstream reset/)).toBeVisible()
    expect(screen.queryByText('system_prompt')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看 Trace' }))
    expect(await screen.findByText(/已打开分析 analysis/)).toBeVisible()
  })
})

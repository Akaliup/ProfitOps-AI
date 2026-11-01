import { test, expect } from '@playwright/test'
import { Workspace } from './workspace'

test('真实 API：分析、越权拦截、审批、模拟执行、幂等与审计', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const workspace = new Workspace(page)
  await workspace.open()
  await expect(page.getByRole('img', { name: /每日销售额和净利润趋势/ })).toBeVisible()
  await page.screenshot({ path: '../docs/screenshots/overview.png' })
  await page.getByRole('button', { name: '查看数据表' }).click()
  await expect(page.getByRole('table', { name: '每日销售与净利润' })).toBeVisible()
  await page.getByRole('button', { name: '查看趋势图' }).click()
  const id = await workspace.analyse()
  const unauthorized = await page.request.post(`/api/recommendations/${id}/approve`, { headers: { Authorization: 'Bearer demo-analyst', 'X-Role': 'approver' }, data: { reason: '浏览器验证越权拦截' } })
  expect(unauthorized.status()).toBe(403)
  await expect(page.getByRole('button', { name: '批准建议' })).toHaveCount(0)
  await workspace.switchApprover()
  await page.getByRole('button', { name: '批准建议' }).click()
  await page.getByRole('button', { name: '模拟执行已批准动作' }).click()
  await expect(page.getByRole('heading', { name: /模拟执行结果/ })).toBeVisible()
  await page.getByRole('button', { name: '再次请求 · 验证幂等' }).click()
  await expect(page.getByText(/重复请求已命中原执行记录/)).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '../docs/screenshots/approval.png' })
  await page.getByRole('heading', { name: /模拟执行结果/ }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../docs/screenshots/execution.png' })
  await page.getByRole('button', { name: '审计日志', exact: true }).click()
  await expect(page.getByRole('table')).toBeVisible()
  const response = await page.request.get(`/api/audit-logs?object_id=${id}`, { headers: { Authorization: 'Bearer demo-approver' } })
  const logs = await response.json()
  expect(logs.items.some((row: { action: string }) => row.action === 'execution.replayed')).toBe(true)
  expect(errors).toEqual([])
})

test('Agent Trace：原生轮次、失败调用与移动端布局可见', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const period = { start_date: '2026-10-01', end_date: '2026-10-30' }
  const previousPeriod = { start_date: '2026-09-01', end_date: '2026-09-30' }
  const totals = {
    sales: 120000, discounts: 0, refunds: 0, cogs: 36000, ad_spend: 45000, fulfillment: 14000,
    platform_fees: 6000, other_costs: 0, orders: 140, units: 140, net_sales: 120000,
    net_profit: -26000, margin_pct: -21.67,
  }
  const traceAnalysis = {
    id: 'analysis-trace', status: 'waiting_approval', request: { period, store_id: null },
    steps: [{ name: 'llm_root_cause_analysis', label: 'DeepSeek 根因分析', status: 'completed', started_at: '2026-11-01T00:00:00Z', request_id: 'trace-request', phase: 'analysis', duration_ms: 800.5, summary: '工具执行失败，已降级' }],
    result: {
      metrics: {
        period, previous_period: previousPeriod, store_id: null, currency: 'USD', current: totals,
        previous: { ...totals, sales: 100000, ad_spend: 10000, net_profit: 45000 }, changes: { net_profit: { absolute: -26000, pct: -57.78 } },
        anomaly: true, bridge_verified: true, source: 'playwright trace fixture',
        drivers: [{ metric: 'ad_spend', label: '广告支出', previous: 10000, current: 45000, contribution: -35000, source: 'daily_operations.ad_spend' }],
        trend: [], sku_breakdown: [],
        data_quality: { complete: true, expected_records: 360, observed_records: 360, missing_records: 0, incomplete_records: 0, missing_examples: [], issues: [] },
      },
      findings: { conclusion: '销售额上涨、净利润下降。广告支出是最大负向贡献项。', evidence: [{ id: 'ev-ad_spend', label: '广告支出', previous: 10000, current: 45000, contribution: -35000, source: 'daily_operations.ad_spend' }], primary_driver: { metric: 'ad_spend', label: '广告支出', previous: 10000, current: 45000, contribution: -35000, source: 'daily_operations.ad_spend' } },
      engine: 'deterministic-rules-v1',
      llm: {
        status: 'deterministic_fallback', configured: true, provider: 'deepseek', model: 'deepseek-flash',
        reason: 'llm_tool_execution_failed', attempts: 2, latency_ms: 800.5,
        usage: { prompt_tokens: 2200, completion_tokens: 300, total_tokens: 2500 }, output: null,
        error: { code: 'llm_tool_execution_failed', message: '工具执行失败：模拟账本不可用', retryable: false },
        native_tools: true, termination_reason: 'llm_tool_execution_failed',
        rounds: [
          { round: 1, finish_reason: 'tool_calls', tool_call_count: 1, latency_ms: 390.5, usage: { prompt_tokens: 1000, completion_tokens: 100, total_tokens: 1100 } },
          { round: 2, finish_reason: 'tool_calls', tool_call_count: 1, latency_ms: 410, usage: { prompt_tokens: 1200, completion_tokens: 200, total_tokens: 1400 } },
        ],
        tool_calls: [
          { round: 1, tool_call_id: 'call-profit', name: 'get_profit_analysis', arguments: { period, store_id: null }, ok: true, result_summary: 'ok; net_profit=-26000; anomaly=True; evidence=8', error_code: null, latency_ms: 41.2 },
          { round: 2, tool_call_id: 'call-sku', name: 'get_sku_breakdown', arguments: { period, store_id: null }, ok: false, result_summary: '工具调用失败', error_code: 'llm_tool_execution_failed', latency_ms: 37.9 },
        ],
      },
    },
    error: null, recommendation_id: null, request_id: 'trace-request', created_at: '2026-11-01T00:00:00Z',
  }
  await page.route('**/api/analyses', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    await route.fulfill({ json: { items: [traceAnalysis] } })
  })
  await page.goto('/')
  await expect(page.getByText('销售在增长，利润却没有跟上。')).toBeVisible()
  await page.getByRole('button', { name: /Agent 工作台/ }).click()
  const trace = page.getByRole('region', { name: 'Agent 执行轨迹' })
  await expect(trace).toBeVisible()
  await expect(trace.getByText('ROUND 1')).toBeVisible()
  await expect(trace.getByText('ROUND 2')).toBeVisible()
  await expect(trace.getByText('get_profit_analysis')).toBeVisible()
  await expect(trace.getByText('get_sku_breakdown')).toBeVisible()
  await expect(trace.getByText('✓ 成功')).toHaveCount(1)
  await expect(trace.getByText('× 失败')).toHaveCount(1)
  await expect(trace.getByText('错误码：llm_tool_execution_failed')).toBeVisible()
  await expect(trace.getByText('工具执行失败', { exact: true })).toBeVisible()
  await expect(trace.getByText('2500')).toBeVisible()
  await page.screenshot({ path: '../docs/screenshots/agent-trace.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await expect(trace).toBeVisible()
  await page.screenshot({ path: '../docs/screenshots/agent-trace-mobile.png', fullPage: true })
  expect(errors).toEqual([])
})

test('拒绝分支和手机视图保持可用', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const workspace = new Workspace(page)
  await workspace.open()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const salesValue = await page.getByText('US$45,600.00', { exact: true }).boundingBox()
  expect(salesValue).not.toBeNull()
  expect(salesValue!.x + salesValue!.width).toBeLessThanOrEqual(390)
  await expect(page.getByRole('img', { name: /每日销售额和净利润趋势/ })).toBeVisible()
  await page.screenshot({ path: '../docs/screenshots/mobile.png' })
  const id = await workspace.analyse()
  await workspace.switchApprover()
  await page.getByLabel('审批意见').fill('先补充活动级转化率，暂不执行')
  await page.getByRole('button', { name: '拒绝建议' }).click()
  await expect(page.getByText('审批意见：先补充活动级转化率，暂不执行')).toBeVisible()
  const response = await page.request.get(`/api/recommendations/${id}`, { headers: { Authorization: 'Bearer demo-approver' } })
  expect((await response.json()).status).toBe('rejected')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

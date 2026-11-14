import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { IntelligencePage } from './intelligence-page'

afterEach(() => vi.unstubAllGlobals())
it('requests a real weekly report and shows API errors instead of fabricated results', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({error: {message: '数据不可用', code: 'unavailable'}}), {status: 503}))
  vi.stubGlobal('fetch', fetch)
  render(<IntelligencePage token='demo-analyst'/>)
  await userEvent.click(screen.getByRole('button', {name: '生成并保存周报'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('数据不可用')
  expect(fetch.mock.calls.length).toBe(1)
  expect(screen.queryByText('利润贡献桥')).not.toBeInTheDocument()
})

const snapshot = {
  id: 'report-a', period: {start_date: '2020-01-06', end_date: '2020-01-12'},
  metrics: {data_quality: {complete: true, issues: []}, current: {sales: 16001, net_profit: 6000, margin_pct: 25}, changes: {net_profit: {absolute: 1000}}, drivers: []},
  findings: {conclusion: '利润贡献'}, stores: [{store_id: 'us-store', name: '美店', current: {sales: 16001, net_profit: 6000, ad_spend: 1000}, profit_delta: 1000, risk_skus: 0}],
  inventory: {items: [], coverage_note: ''}, sku_diagnostics: [{store_id: 'us-store', sku: 'BAG-01', profit_delta: 1000, ad_spend_delta: 100, refund_delta: 0, ad_growth_alert: false}],
  evidence: [{id: 'ev-sales', current: 16001}], source_rows: [], limitations: [], analysis: {status: 'not_requested', claims: []},
}
const reply = {id: 'reply', role: 'assistant', status: 'llm_success', reason: null, content: '已核验', created_at: '2020-01-13T00:00:00Z',
  claims: [{explanation: '销售额见证据', evidence_ids: ['ev-sales', 'us-store:store', 'us-store:sku:BAG-01']}]}
function json(data: unknown, status = 200) {return new Response(JSON.stringify(data), {status})}

it('sends the generation store independently of the inventory filter', async () => {
  const fetch = vi.fn(async () => json(snapshot))
  vi.stubGlobal('fetch', fetch)
  render(<IntelligencePage token='demo-analyst' stores={[{id: 'us-store', name: '美店'}]}/>)
  await userEvent.selectOptions(screen.getByLabelText('生成店铺'), 'us-store')
  // Fail this request intentionally: inspect its body without mounting a chat.
  fetch.mockResolvedValueOnce(json({error: {message: '测试错误'}}, 503))
  await userEvent.click(screen.getByRole('button', {name: '生成并保存周报'}))
  const calls = fetch.mock.calls as unknown as [string, RequestInit][]
  expect(JSON.parse(calls[0][1].body as string)).toMatchObject({store_id: 'us-store', use_llm: false})
})

it('restores saved chat and highlights original, store and SKU citations', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) return json({thread: {id: 'thread'}, items: [reply]})
    if (url.endsWith('/report-a')) return json(snapshot)
    return json({items: [{id: 'report-a', period: snapshot.period}]})
  }))
  const {container} = render(<IntelligencePage token='demo-analyst'/>)
  await userEvent.click(screen.getByRole('button', {name: '读取历史周报'}))
  await userEvent.selectOptions(screen.getByLabelText('历史周报'), 'report-a')
  for (const id of reply.claims[0].evidence_ids) {
    await userEvent.click(await screen.findByRole('button', {name: `查看证据 ${id}`}))
    expect(container.querySelector(`[data-evidence-id="${id}"]`)).toHaveAttribute('data-highlighted', 'true')
  }
  expect(container.querySelector('details')).toHaveAttribute('open')
})

it('highlights visible inventory and profit bridge rows for chat citations', async () => {
  const report = {...snapshot,
    metrics: {...snapshot.metrics, drivers: [{metric: 'sales', label: '销售额', contribution: 1000}]},
    inventory: {items: [{evidence_id: 'inventory-test', sku: 'BAG-01', store_id: 'us-store',
      on_hand: 2, available: 1, daily_sales: '1', coverage_days: '1', lead_time_days: 7,
      timely_inbound: 0, risk: 'CRITICAL', reorder_quantity: 12}], coverage_note: ''},
    evidence: [...snapshot.evidence, {id: 'inventory-test', value: {on_hand: 2}}],
  }
  const citedReply = {...reply, claims: [{explanation: '参见库存和利润桥', evidence_ids: ['inventory-test', 'ev-sales']}]}
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/messages')
    ? json({thread: {id: 'thread'}, items: [citedReply]}) : json(report)))
  const {container} = render(<IntelligencePage token='demo-analyst'/> )
  await userEvent.click(screen.getByRole('button', {name: '生成并保存周报'}))
  for (const [id, tag] of [['inventory-test', 'tr'], ['ev-sales', 'p']]) {
    await userEvent.click(await screen.findByRole('button', {name: `查看证据 ${id}`}))
    const target = container.querySelector(`[data-evidence-id="${id}"]`)
    expect(target?.tagName.toLowerCase()).toBe(tag)
    expect(target).toHaveAttribute('data-highlighted', 'true')
    expect(target).toHaveFocus()
    expect(target?.closest('details')).toBeNull()
  }
  expect(container.querySelector('details')).not.toHaveAttribute('open')
  expect(container.querySelector('tr[data-evidence-id="inventory-test"]')).toHaveAttribute('data-highlighted', 'false')
})

it('posts only the question and renders the API failure without fabricated answers', async () => {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/messages') && init?.method === 'POST') return json({error: {message: '追问不可用'}}, 503)
    if (url.endsWith('/messages')) return json({thread: null, items: []})
    return json(snapshot)
  })
  vi.stubGlobal('fetch', fetch)
  render(<IntelligencePage token='demo-analyst'/>)
  await userEvent.click(screen.getByRole('button', {name: '生成并保存周报'}))
  const input = await screen.findByLabelText('周报问题')
  await waitFor(() => expect(input).not.toBeDisabled())
  await userEvent.type(input, '利润变化是什么')
  await userEvent.click(screen.getByRole('button', {name: '发送追问'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('追问不可用')
  expect(fetch).toHaveBeenCalledWith('/api/weekly-reports/report-a/messages', expect.objectContaining({method: 'POST', body: JSON.stringify({question: '利润变化是什么'})}))
  expect(screen.queryByRole('button', {name: '查看证据 ev-sales'})).not.toBeInTheDocument()
})

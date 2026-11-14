import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportPage } from './import-page'
import type { ImportSummary } from '../../types'

const csv = [
  'order_id,store_id,sku,date,quantity,sales,discounts,refunds,cogs,fulfillment,platform_fees,other_costs',
  'IMP-1,us-store,TRAVEL-BAG-01,2026-11-10,1,49.00,3.92,0,16.00,6.50,7.35,0.90',
].join('\n')

function summary(overrides: Partial<ImportSummary> = {}): ImportSummary {
  return {
    id: null, kind: 'orders', filename: 'orders.csv', status: 'ready', can_commit: true,
    replayed: false, content_sha256: 'a'.repeat(64), total_rows: 1, accepted_rows: 1,
    skipped_rows: 0, rejected_rows: 0, duplicate_rows: 0,
    totals: {
      rows: 1, sales: 4900, discounts: 392, refunds: 0, net_sales: 4508, cogs: 1600,
      ad_spend: 0, fulfillment: 650, platform_fees: 735, other_costs: 90, net_profit: 1433,
      inventory: 0, inbound_inventory: 0,
    },
    issues: [], issue_counts: {},
    checks: [
      { code: 'parse_complete', ok: true, detail: '共 1 行，拒绝 0 行' },
      { code: 'row_balance', ok: true, detail: '每行净销售额与贡献利润按整数美分逐行加总' },
    ],
    created_by: null, created_at: null,
    ...overrides,
  }
}

let preview: ImportSummary
let commit: ImportSummary
let requests: { path: string; init?: RequestInit }[]

beforeEach(() => {
  preview = summary({ preview_rows: [{ store_id: 'us-store', sku: 'TRAVEL-BAG-01' }] })
  commit = summary({ id: 'batch-1', status: 'committed', can_commit: false })
  requests = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    requests.push({ path, init })
    if (path.startsWith('/api/imports?limit=')) {
      return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (path.startsWith('/api/data/orders/preview?')) {
      return new Response(JSON.stringify(preview), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (path.startsWith('/api/data/orders/commit?')) {
      return new Response(JSON.stringify(commit), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({ error: { code: 'not_found', message: 'missing' } }), { status: 404 })
  }))
})

describe('ImportPage', () => {
  it('uploads a csv, previews the reconciliation, then commits', async () => {
    const user = userEvent.setup()
    const onImported = vi.fn()
    render(<ImportPage token='demo-approver' isApprover onImported={onImported} />)

    const file = new File([csv], 'orders.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => csv })
    await user.upload(screen.getByLabelText('选择 CSV 文件'), file)
    await screen.findByText('orders.csv')

    await user.click(screen.getByRole('button', { name: /校验文件/ }))
    expect(await screen.findByText('校验通过')).toBeVisible()
    expect(screen.getByLabelText('数据预览')).toHaveTextContent('TRAVEL-BAG-01')
    expect(screen.getByText(/45\.08/)).toBeVisible()
    expect(screen.getByText(/14\.33/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: /确认导入 1 行/ }))
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('已写入账本')).toBeVisible()
    expect(screen.getByText(/已写入 1 行 已保存来源与批次/)).toBeVisible()

    const commitRequest = requests.find(item => item.path.startsWith('/api/data/orders/commit?'))
    expect(commitRequest?.init?.body).toBe(csv)
    expect(new Headers(commitRequest?.init?.headers).get('X-Filename')).toBe('orders.csv')
  })

  it('shows row issues and keeps the commit action disabled', async () => {
    preview = summary({
      status: 'blocked', can_commit: false, accepted_rows: 0, rejected_rows: 1,
      issues: [{ row: 2, field: 'sales', code: 'invalid_money', message: 'sales 必须是最多两位小数的金额' }],
      issue_counts: { invalid_money: 1 },
    })
    const user = userEvent.setup()
    render(<ImportPage token='demo-analyst' isApprover={false} onImported={() => {}} />)

    const file = new File([csv], 'orders.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => csv })
    await user.upload(screen.getByLabelText('选择 CSV 文件'), file)
    await user.click(screen.getByRole('button', { name: /校验文件/ }))

    expect(await screen.findByText('未通过校验')).toBeVisible()
    expect(screen.getByText('金额格式')).toBeVisible()
    expect(screen.getByText('sales 必须是最多两位小数的金额')).toBeVisible()
    expect(screen.getByText('当前身份只读 切换到审批负责人后可写入')).toBeVisible()
  })

  const amazonAdapter = {
    id: 'amazon-orders', label: 'Amazon 风格订单报表', kind: 'orders' as const,
    description: '离线字段映射，不连接卖家后台',
    mapping: {store_id: 'store_id', sku: 'sku', order_id: 'order-id', gross_sales: 'item-price'},
  }

  it('sends the selected adapter on preview and commit and shows its mapping', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      requests.push({path, init})
      if (path.startsWith('/api/imports?limit=')) return new Response(JSON.stringify({items: []}), {status: 200})
      if (path.startsWith('/api/data/adapters')) return new Response(JSON.stringify({items: [amazonAdapter]}), {status: 200})
      if (path.startsWith('/api/data/orders/preview?')) return new Response(JSON.stringify(preview), {status: 200})
      if (path.startsWith('/api/data/orders/commit?')) return new Response(JSON.stringify(commit), {status: 200})
      return new Response(JSON.stringify({error: {code: 'not_found', message: 'missing'}}), {status: 404})
    }))
    render(<ImportPage token='demo-approver' isApprover onImported={() => {}} />)
    expect(await screen.findByRole('option', {name: 'Amazon 风格订单报表'})).toBeVisible()
    await user.selectOptions(screen.getByLabelText('平台报表适配器'), 'amazon-orders')
    expect(screen.getByLabelText('字段映射')).toHaveValue(JSON.stringify(amazonAdapter.mapping, null, 2))
    const file = new File([csv], 'orders.csv', {type: 'text/csv'})
    Object.defineProperty(file, 'text', {value: async () => csv})
    await user.upload(screen.getByLabelText('选择 CSV 文件'), file)
    await user.click(screen.getByRole('button', {name: /校验文件/}))
    expect(await screen.findByText('校验通过')).toBeVisible()
    await user.click(screen.getByRole('button', {name: /确认导入 1 行/}))
    const previewRequest = requests.find(item => item.path.startsWith('/api/data/orders/preview?'))
    const commitRequest = requests.find(item => item.path.startsWith('/api/data/orders/commit?'))
    expect(previewRequest?.path).toContain('adapter=amazon-orders')
    expect(commitRequest?.path).toContain('adapter=amazon-orders')
    expect(previewRequest?.path).toContain('mapping=')
  })

  it('does not present a 422 adapter validation failure as success', async () => {
    const user = userEvent.setup()
    const onImported = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      if (path.startsWith('/api/imports?limit=')) return new Response(JSON.stringify({items: []}), {status: 200})
      if (path.startsWith('/api/data/adapters')) return new Response(JSON.stringify({items: [amazonAdapter]}), {status: 200})
      if (path.includes('/preview')) return new Response(JSON.stringify({error: {code: 'invalid_adapter', message: '适配器与数据类型不匹配'}}), {status: 422})
      return new Response(JSON.stringify({error: {code: 'not_found', message: 'missing'}}), {status: 404})
    }))
    render(<ImportPage token='demo-approver' isApprover onImported={onImported} />)
    await screen.findByRole('option', {name: 'Amazon 风格订单报表'})
    await user.selectOptions(screen.getByLabelText('平台报表适配器'), 'amazon-orders')
    const file = new File([csv], 'orders.csv', {type: 'text/csv'})
    Object.defineProperty(file, 'text', {value: async () => csv})
    await user.upload(screen.getByLabelText('选择 CSV 文件'), file)
    await user.click(screen.getByRole('button', {name: /校验文件/}))
    expect(await screen.findByRole('alert')).toHaveTextContent('适配器与数据类型不匹配')
    expect(screen.queryByText('校验通过')).not.toBeInTheDocument()
    expect(screen.queryByText('已写入账本')).not.toBeInTheDocument()
    expect(screen.getByText('还没有校验结果')).toBeVisible()
    expect(onImported).not.toHaveBeenCalled()
  })
})

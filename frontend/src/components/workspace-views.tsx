import type { FormEvent } from 'react'
import type { AccessView, CampaignReport, EvalList, EvalReport, InventoryReport } from '../types'
import { money } from '../format'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card, CardContent } from './ui/card'
import { Input } from './ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'

const cap = { own: '已有能力', part: '部分已有', plan: '规划中' } as const

export function AssistantsView({ onOpen, onInvestigate }: { onOpen: (page: 'overview' | 'investigate' | 'inventory' | 'ads' | 'approvals') => void; onInvestigate: () => void }) {
  const agents = [
    { id: 'ops', name: '经营分析助手', status: 'own' as const, desc: '利润拆解、异常归因和带证据的建议。走现有 LangGraph，未配置模型时明确降级。', tools: ['compute_profit', 'get_sku_breakdown', 'search_knowledge'], tasks: [
      { title: '销售上涨，利润为什么下降？', run: onInvestigate, enabled: true },
      { title: '查看分店贡献', run: () => onOpen('overview'), enabled: true },
    ]},
    { id: 'stock', name: '库存助手', status: 'part' as const, desc: '读取已导入的库存快照并生成补货提案。真实 ERP 未连接。', tools: ['list_inventory', 'draft_replenish'], tasks: [
      { title: '哪些 SKU 需要补货？', run: () => onOpen('inventory'), enabled: true },
    ]},
    { id: 'ads', name: '广告分析助手', status: 'part' as const, desc: '每日广告账单可生成预算模拟。活动级 ROAS 只有在不与账单冲突的广告 CSV 存在时才显示。', tools: ['ledger_ad_spend', 'draft_budget_change'], tasks: [
      { title: '查看花费并生成预算模拟', run: () => onOpen('ads'), enabled: true },
    ]},
    { id: 'product', name: '商品运营助手', status: 'plan' as const, desc: '跟价、主图和转化不在本仓库主链路。调价只做服务端毛利校验，不改价。', tools: ['（规划）转化漏斗'], tasks: [
      { title: '校验拟调价是否破红线', run: () => onOpen('approvals'), enabled: true },
    ]},
    { id: 'cs', name: '客服助手', status: 'plan' as const, desc: '退款归类和差评回复尚未实现，不能从这里发起任务。', tools: ['（规划）退款归因'], tasks: []},
    { id: 'finance', name: '财务助手', status: 'plan' as const, desc: '自动周报对账仍是规划。利润数字由确定性账本计算，不另起财务 Agent。', tools: ['（规划）费用对账'], tasks: []},
  ]
  return <div className='space-y-4'>
    <p className='rounded-lg border border-[#E2DDD3] bg-[#FFFEFA] px-4 py-3 text-sm text-[#5F675F]'>这里不是多 Agent 编排。已有能力会调用现有后端；标为规划中的助手不能启动，也不会假装已经完成。</p>
    <div className='grid gap-4 lg:grid-cols-2'>
      {agents.map(agent => <Card key={agent.id} className='gap-0 py-0'>
        <CardContent className='space-y-3 px-5 py-4'>
          <div className='flex items-center justify-between gap-2'>
            <h3 className='text-base font-semibold'>{agent.name}</h3>
            <Badge variant={agent.status === 'own' ? 'success' : agent.status === 'part' ? 'warning' : 'secondary'}>{cap[agent.status]}</Badge>
          </div>
          <p className='text-sm leading-relaxed text-muted-foreground'>{agent.desc}</p>
          <div className='flex flex-wrap gap-1.5'>{agent.tools.map(tool => <code key={tool} className='rounded border bg-muted px-1.5 py-0.5 text-[11px]'>{tool}</code>)}</div>
          {agent.tasks.length ? agent.tasks.map(task => <div key={task.title} className='flex items-center justify-between gap-3 border-t pt-3'>
            <span className='text-sm'>{task.title}</span>
            <Button size='sm' onClick={task.run} disabled={!task.enabled}>打开</Button>
          </div>) : <p className='border-t pt-3 text-xs text-muted-foreground'>任务入口已禁用。没有后台任务，也没有伪造的会话。</p>}
        </CardContent>
      </Card>)}
    </div>
  </div>
}

export function InventoryBoard({ report, busy, onPropose }: { report: InventoryReport | null; busy: boolean; onPropose: () => void }) {
  const rows = report?.items ?? []
  const suggested = rows.filter(row => (row.reorder_quantity ?? 0) > 0 && !['insufficient_data', 'no_demand'].includes(row.status) && ['CRITICAL', 'HIGH', 'MEDIUM'].includes(row.risk))
  return <div className='grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]'>
    <Card className='gap-0 overflow-hidden py-0'>
      <div className='border-b px-5 py-4'>
        <h3 className='text-base font-semibold'>库存一览</h3>
        <p className='text-xs text-muted-foreground'>{report?.coverage_note ?? '导入库存 CSV 后，按账本时点计算可售天数和补货量。'}</p>
      </div>
      {!rows.length ? <p className='px-5 py-8 text-sm text-muted-foreground'>当前筛选下没有库存快照。可在「知识与数据」提交演示库存 CSV。</p> : <Table>
        <TableHeader><TableRow><TableHead>SKU</TableHead><TableHead>在库</TableHead><TableHead>可售天数</TableHead><TableHead>风险</TableHead><TableHead>建议补货</TableHead></TableRow></TableHeader>
        <TableBody>{rows.map(row => <TableRow key={`${row.store_id}-${row.sku}`}>
          <TableCell><strong>{row.sku}</strong><span className='block text-xs text-muted-foreground'>{row.store_id}</span></TableCell>
          <TableCell className='tabular-nums'>{row.on_hand}</TableCell>
          <TableCell className='tabular-nums'>{row.coverage_days ?? '—'}</TableCell>
          <TableCell>{row.risk}</TableCell>
          <TableCell className='tabular-nums'>{row.reorder_quantity ?? '—'}</TableCell>
        </TableRow>)}</TableBody>
      </Table>}
    </Card>
    <Card className='gap-0 py-0'>
      <CardContent className='space-y-3 px-5 py-4'>
        <h3 className='text-base font-semibold'>模拟补货提案</h3>
        <p className='text-sm text-muted-foreground'>只纳入服务端算出的补货量。超储和数据不足不会进入提案，也不会向供应商下单。</p>
        {suggested.map(row => <div key={`${row.store_id}-${row.sku}`} className='text-sm'>{row.sku} · {row.store_id} · {row.reorder_quantity} 件</div>)}
        {!suggested.length && <p className='text-sm text-muted-foreground'>没有可建议补货的 SKU。</p>}
        <Button onClick={onPropose} disabled={busy || !suggested.length}>{busy ? '提交中…' : '生成模拟补货提案'}</Button>
      </CardContent>
    </Card>
  </div>
}

export function AdsBoard({ report, busy, onPropose }: { report: CampaignReport | null; busy: boolean; onPropose: () => void }) {
  const items = report?.items ?? []
  return <div className='space-y-4'>
    <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
      <Card className='px-5 py-4'><span className='text-sm text-muted-foreground'>活动花费</span><strong className='mt-1 block text-2xl tabular-nums'>{items.length ? money(report?.spend ?? 0) : '账本可见'}</strong></Card>
      <Card className='px-5 py-4'><span className='text-sm text-muted-foreground'>归因销售额</span><strong className='mt-1 block text-2xl tabular-nums'>{items.length ? money(report?.attributed_sales ?? 0) : '—'}</strong></Card>
      <Card className='px-5 py-4'><span className='text-sm text-muted-foreground'>综合 ROAS</span><strong className='mt-1 block text-2xl tabular-nums'>{report?.roas ?? '—'}</strong></Card>
      <Card className='px-5 py-4'><span className='text-sm text-muted-foreground'>低于 {report?.threshold ?? 2.5}</span><strong className='mt-1 block text-2xl tabular-nums'>{report?.weak ?? 0} 条</strong></Card>
    </div>
    <p className='text-xs leading-relaxed text-muted-foreground'>{report?.note}</p>
    <div className='grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(280px,0.7fr)]'>
      <Card className='gap-0 overflow-hidden py-0'>
        <div className='border-b px-5 py-4'><h3 className='font-semibold'>计划明细</h3></div>
        {!items.length ? <p className='px-5 py-8 text-sm text-muted-foreground'>没有活动级广告行。种子数据的广告费在每日经营记录中，页面不会编造 ROAS。预算模拟仍可按账本广告费生成。</p> : <Table>
          <TableHeader><TableRow><TableHead>计划</TableHead><TableHead>花费</TableHead><TableHead>归因销售</TableHead><TableHead>ROAS</TableHead><TableHead>上期 ROAS</TableHead></TableRow></TableHeader>
          <TableBody>{items.map(row => <TableRow key={row.evidence_id}>
            <TableCell>{row.campaign_name}<span className='block text-xs text-muted-foreground'>{row.store_id}</span></TableCell>
            <TableCell className='tabular-nums'>{money(row.ad_spend)}</TableCell>
            <TableCell className='tabular-nums'>{money(row.attributed_sales)}</TableCell>
            <TableCell className='tabular-nums'>{row.roas ?? '—'}</TableCell>
            <TableCell className='tabular-nums'>{row.previous_roas ?? '—'}</TableCell>
          </TableRow>)}</TableBody>
        </Table>}
      </Card>
      <Card className='px-5 py-4'>
        <h3 className='font-semibold'>预算模拟</h3>
        <p className='mt-2 text-sm text-muted-foreground'>当广告花费是利润下降的最大负向贡献时，服务端按参考日预算下调最多 30%，并进入同一条审批流。不调用广告后台。</p>
        <Button className='mt-4' onClick={onPropose} disabled={busy}>{busy ? '提交中…' : '生成预算调整提案'}</Button>
      </Card>
    </div>
  </div>
}

export function SettingsView({ access }: { access: AccessView | null }) {
  if (!access) return <p className='text-sm text-muted-foreground'>正在读取服务端权限…</p>
  return <div className='grid gap-4 xl:grid-cols-2'>
    <Card className='gap-0 overflow-hidden py-0'>
      <div className='border-b px-5 py-4'>
        <h3 className='font-semibold'>角色与工具权限</h3>
        <p className='text-xs text-muted-foreground'>当前身份 {access.actor.role} · 租户 {access.actor.tenant_id} · {access.note}</p>
      </div>
      <div className='overflow-auto'>
        <Table>
          <TableHeader><TableRow><TableHead>角色</TableHead>{access.columns.map(column => <TableHead key={column}>{column}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{access.rows.map(row => <TableRow key={row.role}>
            <TableCell>{row.label}{row.implemented ? '' : ' · 规划中'}{row.current ? ' · 当前' : ''}</TableCell>
            {row.cells.map((allowed, index) => <TableCell key={access.columns[index]}>{row.implemented ? (allowed ? '允许' : '—') : '禁用'}</TableCell>)}
          </TableRow>)}</TableBody>
        </Table>
      </div>
    </Card>
    <Card className='px-5 py-4'>
      <h3 className='font-semibold'>集成实际状态</h3>
      <div className='mt-3 space-y-3'>{access.integrations.map(item => <div key={item.name} className='border-b pb-3 last:border-b-0'>
        <div className='flex items-center justify-between gap-2'><strong className='text-sm'>{item.name}</strong><Badge variant={item.state === 'available' || item.state === 'configured' ? 'success' : 'secondary'}>{item.state}</Badge></div>
        <p className='mt-1 text-xs leading-relaxed text-muted-foreground'>{item.detail}</p>
      </div>)}</div>
      <p className='mt-3 text-xs text-muted-foreground'>全部执行强制 simulation。本页不提供密钥、账号注册或外部系统开关。</p>
    </Card>
  </div>
}

export function EvalPanel({ report, history, busy, onRun }: { report: EvalReport | null; history: EvalList | null; busy: boolean; onRun: () => void }) {
  const latest = report ?? history?.items[0]?.details ?? null
  return <Card className='gap-0 py-0'>
    <div className='flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4'>
      <div>
        <h3 className='font-semibold'>可复现评测</h3>
        <p className='text-xs text-muted-foreground'>{history?.note ?? '运行后才有结果。'}</p>
      </div>
      <Button onClick={onRun} disabled={busy}>{busy ? '计算中…' : '运行离线评测'}</Button>
    </div>
    {!latest ? <p className='px-5 py-6 text-sm text-muted-foreground'>尚未运行。这里不会显示手写通过率。</p> : <div className='px-5 py-4'>
      <p className='text-sm'>通过 {latest.passed} / {latest.total} · 失败 {latest.failed} · 模型调用 {latest.llm_calls} · {latest.engine}</p>
      <p className='mt-1 text-xs text-muted-foreground'>{latest.note}</p>
      <Table className='mt-3'>
        <TableHeader><TableRow><TableHead>编号</TableHead><TableHead>场景</TableHead><TableHead>结果</TableHead><TableHead>说明</TableHead></TableRow></TableHeader>
        <TableBody>{latest.cases.map(item => <TableRow key={item.id}>
          <TableCell>{item.id}</TableCell><TableCell>{item.scene}</TableCell>
          <TableCell>{item.ok ? '通过' : '失败'}</TableCell><TableCell>{item.detail}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </div>}
  </Card>
}

export function PriceCheckForm({ stores, busy, onSubmit }: { stores: { id: string; name: string }[]; busy: boolean; onSubmit: (storeId: string, sku: string, cents: number) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const dollars = Number(data.get('price'))
    if (!Number.isFinite(dollars) || dollars <= 0) return
    onSubmit(String(data.get('store')), String(data.get('sku')).trim(), Math.round(dollars * 100))
  }
  return <form className='grid gap-3 rounded-lg border bg-card p-4 md:grid-cols-[1fr_1fr_140px_auto] md:items-end' onSubmit={submit}>
    <label className='text-xs text-muted-foreground'>店铺
      <select name='store' aria-label='调价店铺' className='mt-1 h-9 w-full rounded-md border bg-background px-2 text-sm'>
        {stores.map(store => <option key={store.id} value={store.id}>{store.name}</option>)}
      </select>
    </label>
    <label className='text-xs text-muted-foreground'>SKU
      <Input name='sku' aria-label='调价 SKU' defaultValue='TRAVEL-BAG-01' className='mt-1' />
    </label>
    <label className='text-xs text-muted-foreground'>拟售价（USD）
      <Input name='price' aria-label='拟售价' defaultValue='1.00' className='mt-1' />
    </label>
    <Button type='submit' disabled={busy || !stores.length}>校验毛利红线</Button>
  </form>
}

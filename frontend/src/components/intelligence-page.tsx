import { useEffect, useRef, useState } from 'react'
import { WeeklyChat } from './weekly-chat'
import { FileBarChart, LoaderCircle } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { request } from '../api'
import { money } from '../format'
import type { WeeklyIntelligence } from '../types'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'

export function IntelligencePage({token, stores = []}: {token: string; stores?: {id: string; name: string}[]}) {
  const root = useRef<HTMLDivElement>(null)
  const [selectedEvidence, setSelectedEvidence] = useState('')
  const [generationStore, setGenerationStore] = useState('')
  const [history, setHistory] = useState<{id: string; period: {start_date: string; end_date: string}}[]>([])
  const [report, setReport] = useState<WeeklyIntelligence | null>(null)
  const [end, setEnd] = useState('')
  const [store, setStore] = useState('')
  const [risk, setRisk] = useState('')
  const [useLLM, setUseLLM] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function generate() {
    setBusy(true); setError('')
    try { setReport(await request<WeeklyIntelligence>('/api/weekly-reports', token, {method: 'POST', body: JSON.stringify({...(end ? {end_date: end} : {}), use_llm: useLLM, ...(generationStore ? {store_id: generationStore} : {})})})); setSelectedEvidence('') }
    catch (e) {setError(e instanceof Error ? e.message : '生成失败')}
    finally {setBusy(false)}
  }
  const rows = report?.inventory.items.filter(x => (!store || x.store_id === store) && (!risk || x.risk === risk)) ?? []
  async function loadHistory() {
    setBusy(true); setError('')
    try {setHistory((await request<{items: typeof history}>('/api/weekly-reports', token)).items)}
    catch (e) {setError(e instanceof Error ? e.message : '读取历史失败')}
    finally {setBusy(false)}
  }
  async function openReport(id: string) {
    if (!id) return
    setBusy(true); setError('')
    try {setReport(await request<WeeklyIntelligence>(`/api/weekly-reports/${id}`, token)); setSelectedEvidence('')}
    catch (e) {setError(e instanceof Error ? e.message : '读取周报失败')}
    finally {setBusy(false)}
  }
  useEffect(() => {
    if (!selectedEvidence) return
    const target = Array.from(root.current?.querySelectorAll<HTMLElement>('[data-evidence-id]') ?? []).find(x => x.dataset.evidenceId === selectedEvidence)
    const details = target?.closest('details')
    if (details) details.open = true
    target?.scrollIntoView?.({behavior: 'smooth', block: 'center'})
    target?.focus({preventScroll: true})
  }, [selectedEvidence])
  function evidenceProps(id: string) {
    return {'data-evidence-id': id, 'data-highlighted': selectedEvidence === id, tabIndex: -1,
      className: `transition-colors duration-300 ${selectedEvidence === id ? 'bg-blue-100 ring-2 ring-blue-500' : ''}`}
  }
  return <div ref={root} className='intelligence space-y-5'>
    <Card><CardHeader><CardTitle className='flex gap-2'><FileBarChart size={20}/>多店铺经营周报</CardTitle></CardHeader><CardContent className='flex flex-wrap items-center gap-4'>
      <label>生成店铺 <select aria-label='生成店铺' value={generationStore} onChange={e => setGenerationStore(e.target.value)}><option value=''>全部店铺</option>{stores.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      <label>周期结束日 <input aria-label='周期结束日' type='date' value={end} onChange={e => setEnd(e.target.value)}/></label>
      <span className='text-sm text-muted-foreground'>留空为最近完整自然周 UTC</span>
      <label><input type='checkbox' checked={useLLM} onChange={e => setUseLLM(e.target.checked)}/> 请求已配置模型解释</label>
      <Button disabled={busy} onClick={generate}>{busy && <LoaderCircle className='animate-spin'/>}{busy ? '正在计算与核验证据' : '生成并保存周报'}</Button>
    </CardContent></Card>
    <div className='flex flex-wrap gap-3'><Button variant='outline' disabled={busy} onClick={loadHistory}>读取历史周报</Button><select aria-label='历史周报' disabled={busy} value={history.some(x => x.id === report?.id) ? report?.id : ''} onChange={e => openReport(e.target.value)}><option value=''>选择已保存周报</option>{history.map(x => <option key={x.id} value={x.id}>{x.period.start_date} — {x.period.end_date} · {x.id.slice(0, 8)}</option>)}</select></div>
    {error && <p role='alert'>{error}</p>}
    <AnimatePresence mode='wait'><motion.div key={report?.id ?? 'empty'} initial={{opacity: 0, y: 8}} animate={{opacity: 1, y: 0}} exit={{opacity: 0, y: -8}} layout>
    {!report ? <p>导入订单 广告与库存后生成周报 缺失数据会明确标记</p> : <div className='space-y-5'>
      <p>{report.period.start_date} — {report.period.end_date} · {report.analysis.status} {report.analysis.reason}</p>
      {!report.metrics.data_quality.complete && <p role='status' className='text-red-700'>数据不足 以下仅为已导入账本合计 {report.metrics.data_quality.issues.join('；')}</p>}
      <div className='grid gap-4 md:grid-cols-4'>{[['销售额', money(report.metrics.current.sales)], ['净利润', money(report.metrics.current.net_profit)], ['利润环比变化', money(report.metrics.changes.net_profit.absolute)], ['净利润率', `${report.metrics.current.margin_pct ?? '—'}%`]].map(([k,v]) => <Card key={k}><CardHeader>{k}</CardHeader><CardContent className='text-xl'>{v}</CardContent></Card>)}</div>
      <Card><CardHeader><CardTitle>店铺表现</CardTitle></CardHeader><CardContent className='overflow-auto'><table><thead><tr>{['店铺','销售额','利润','利润率','广告花费','利润变化','高风险 SKU'].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{report.stores.map(x => <tr key={x.store_id} {...evidenceProps(x.store_id + ":store")}><td>{x.name}</td><td>{money(x.current.sales)}</td><td>{money(x.current.net_profit)}</td><td>{x.current.margin_pct ?? '—'}%</td><td>{money(x.current.ad_spend)}</td><td>{money(x.profit_delta)}</td><td>{x.risk_skus}</td></tr>)}</tbody></table></CardContent></Card>
      <Card><CardHeader><CardTitle>利润贡献桥</CardTitle></CardHeader><CardContent><p>{report.findings.conclusion}</p>{report.metrics.drivers.map(x => <p key={x.metric} {...evidenceProps(`ev-${x.metric}`)}>{x.label} {money(x.contribution)} <small>ev-{x.metric}</small></p>)}</CardContent></Card>
      <Card><CardHeader><CardTitle>量价与结构拆解</CardTitle></CardHeader><CardContent>
        {report.metrics.decomposition?.status !== 'OK' ? <p className='text-sm text-muted-foreground'>销量基数不足，暂不计算 ASP 与量价贡献</p> : <>
          <p className='text-sm text-muted-foreground'>{report.metrics.decomposition.formula}</p>
          <div className='mt-3 grid gap-2 sm:grid-cols-4'>{(['volume', 'price', 'mix', 'residual'] as const).map(key => {
            const value = report.metrics.decomposition?.contributions?.[key]
            return <div key={key} {...evidenceProps(`ev-${key}`)} className={`rounded border p-3 transition-colors ${selectedEvidence === `ev-${key}` ? 'bg-blue-100 ring-2 ring-blue-500' : ''}`}>
              <span className='text-xs text-muted-foreground'>{key === 'volume' ? '销量效应' : key === 'price' ? '价格效应' : key === 'mix' ? '结构效应' : '残差'}</span>
              <strong className='mt-1 block text-lg'>{value == null ? '—' : money(value)}</strong>
              <small className='text-muted-foreground'>ev-{key}</small>
            </div>
          })}</div>
        </>}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>库存风险与补货建议</CardTitle></CardHeader><CardContent className='overflow-auto'>
        <div className='flex gap-4 mb-4'><select aria-label='库存店铺筛选' value={store} onChange={e => setStore(e.target.value)}><option value=''>全部店铺</option>{report.stores.map(x => <option key={x.store_id} value={x.store_id}>{x.name}</option>)}</select><select aria-label='库存风险筛选' value={risk} onChange={e => setRisk(e.target.value)}>{['','CRITICAL','HIGH','MEDIUM','LOW','INSUFFICIENT_DATA'].map(x => <option key={x} value={x}>{x || '全部风险'}</option>)}</select></div>
        <table><thead><tr>{['SKU','店铺','现有 / 可售','日均销量','覆盖天数','采购周期','及时在途','风险','建议补货'].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{rows.map(x => <tr key={x.evidence_id} {...evidenceProps(x.evidence_id)}><td>{x.sku}</td><td>{x.store_id}</td><td>{x.on_hand} / {x.available}</td><td>{x.daily_sales}</td><td>{x.coverage_days ?? '—'}</td><td>{x.lead_time_days}</td><td>{x.timely_inbound}</td><td>{x.risk}</td><td>{x.reorder_quantity ?? '数据不足'}</td></tr>)}</tbody></table>
        {!rows.length && <p>没有符合筛选条件的库存快照</p>}<p className='mt-3 text-sm'>{report.inventory.coverage_note}</p>
      </CardContent></Card>
      <Card><CardHeader><CardTitle>SKU 诊断</CardTitle></CardHeader><CardContent>{report.sku_diagnostics.map(x => <p key={`${x.store_id}:${x.sku}`} {...evidenceProps(x.store_id + ":sku:" + x.sku)}>{x.store_id} / {x.sku} · 利润变化 {money(x.profit_delta)} · 广告变化 {money(x.ad_spend_delta)} · 退款变化 {money(x.refund_delta)} {x.ad_growth_alert && '广告增长异常'}</p>)}</CardContent></Card>
      <Card><CardHeader><CardTitle>调价建议</CardTitle></CardHeader><CardContent className='overflow-auto'><table><thead><tr><th>店铺</th><th>SKU</th><th>当前基准价</th><th>建议价</th><th>规则</th></tr></thead><tbody>{(report.pricing_suggestions ?? []).map(item => <tr key={item.evidence_id} {...evidenceProps(item.evidence_id)}><td>{item.store_id}</td><td>{item.sku}</td><td>{item.current_price == null ? '数据不足' : money(item.current_price)}</td><td>{item.suggested_price == null ? '数据不足' : money(item.suggested_price)}</td><td>{item.reason_code}</td></tr>)}</tbody></table><p className='mt-3 text-sm text-muted-foreground'>建议由确定性规则生成，仅供观察与模拟，不会写入商品或平台</p></CardContent></Card>
      <Card><CardHeader><CardTitle>广告活动表现</CardTitle></CardHeader><CardContent className='overflow-auto'><table><thead><tr>{['店铺','活动名称 / ID','广告花费','归因销售','归因订单','点击数','曝光量'].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{(report.campaign_diagnostics ?? []).map(item => <tr key={item.evidence_id} {...evidenceProps(item.evidence_id)}><td>{item.store_id}</td><td>{item.campaign_name} <small className='text-muted-foreground'>({item.campaign_id})</small></td><td>{money(item.ad_spend)}</td><td>{money(item.attributed_sales)}</td><td>{item.attributed_orders}</td><td>{item.clicks}</td><td>{item.impressions}</td></tr>)}</tbody></table>{!(report.campaign_diagnostics ?? []).length && <p className='text-sm text-muted-foreground'>本周期无活动明细数据</p>}<p className='mt-3 text-sm text-muted-foreground'>归因销售仅供广告效率参考，不得重复计入利润桥订单销售</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Agent 分析与证据</CardTitle></CardHeader><CardContent>{report.analysis.claims.map((x,i) => <p key={i}>{x.explanation} <small>{x.evidence_ids.join(' · ')}</small></p>)}{!report.analysis.claims.length && <p>当前展示确定性计算结果 模型未生成有效解释</p>}<details><summary>查看计算证据与导入批次</summary>{report.evidence.map(ev => <div key={ev.id} {...evidenceProps(ev.id)}><pre className='overflow-auto text-xs'>{JSON.stringify(ev, null, 2)}</pre></div>)}<pre className='overflow-auto text-xs'>{JSON.stringify(report.source_rows, null, 2)}</pre></details>{report.limitations.map(x => <p key={x} className='text-sm text-muted-foreground'>{x}</p>)}</CardContent></Card>
      <WeeklyChat key={`${token}:${report.id}`} reportId={report.id} token={token} onCitation={setSelectedEvidence}/>
    </div>}
    </motion.div></AnimatePresence>
  </div>
}

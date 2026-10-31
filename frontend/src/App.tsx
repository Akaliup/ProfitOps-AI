import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, Check, CheckCircle2, ChevronDown, CircleDollarSign, Clock3, Database, FileCheck2, Fingerprint, LayoutDashboard, LoaderCircle, Moon, Play, RefreshCw, ShieldCheck, Sparkles, Sun, Terminal, TriangleAlert, X } from 'lucide-react'
import { api, ApiError } from './api'
import type { Analysis, Audit, Demo, Execution, Identity, LLMConfig, LLMRun, Metrics, Recommendation, Scope } from './types'
import { dateTime, llmReasonLabels, llmStatusLabels, money, percent, signedMoney, statusLabels } from './format'
import './styles.css'

const TrendChart = lazy(() => import('./components/TrendChart').then(module => ({ default: module.TrendChart })))

type Page = 'overview' | 'agent' | 'approvals' | 'audit'
const pages = [
  { id: 'overview' as const, label: '经营总览', icon: LayoutDashboard, detail: '把增长，变成利润。' },
  { id: 'agent' as const, label: 'Agent 工作台', icon: Sparkles, detail: '从经营信号，到有据可依的行动。' },
  { id: 'approvals' as const, label: '审批与执行', icon: FileCheck2, detail: '每一步行动，都由你掌控。' },
  { id: 'audit' as const, label: '审计日志', icon: Fingerprint, detail: '让每一次决策都有迹可循。' },
]
const busyStatuses = ['queued', 'running']

function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}><span aria-hidden="true">{['executed', 'completed', 'approved'].includes(value) ? '✓' : ['failed', 'execution_failed', 'rejected'].includes(value) ? '×' : '◷'}</span>{statusLabels[value] ?? value}</span>
}
function Empty({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return <div className="empty"><Database size={28} /><h3>{title}</h3><p>{text}</p>{children}</div>
}

export default function App() {
  const [page, setPage] = useState<Page>('overview')
  const [demo, setDemo] = useState<Demo | null>(null)
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [scope, setScope] = useState<Scope>({ days: 30, storeId: '' })
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [audits, setAudits] = useState<Audit[]>([])
  const [auditQuery, setAuditQuery] = useState('')
  const [auditFilter, setAuditFilter] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [execution, setExecution] = useState<Execution | null>(null)
  const [reason, setReason] = useState('已复核证据，批准受控模拟')
  const [dark, setDark] = useState(false)
  const loadSequence = useRef(0)
  const selected = recommendations.find(item => item.id === selectedId) ?? recommendations[0]
  const pending = recommendations.filter(item => item.status === 'pending_approval').length
  const isApprover = identity?.role === 'approver'

  const showError = useCallback((value: unknown) => {
    if (value instanceof ApiError) setError(`${value.message}${value.requestId ? ` · 请求 ID ${value.requestId}` : ''}`)
    else setError(value instanceof Error ? value.message : '操作失败，请重试')
  }, [])

  useEffect(() => {
    let active = true
    api.demo().then(value => {
      if (!active) return
      setDemo(value)
      setIdentity(value.identities[0] ?? null)
      if (!value.identities.length) { setError('演示认证已关闭，请通过服务配置启用本地演示模式'); setLoading(false) }
    }).catch(value => { if (active) { showError(value); setLoading(false) } })
    return () => { active = false }
  }, [showError])

  const refresh = useCallback(async () => {
    if (!demo || !identity) return
    const sequence = ++loadSequence.current
    setLoading(true)
    try {
      const [summary, history, items, logs] = await Promise.all([
        demo.data_range ? api.summary(identity.token, demo, scope) : Promise.resolve(null),
        api.analyses(identity.token), api.recommendations(identity.token), api.audits(identity.token, auditFilter),
      ])
      if (sequence !== loadSequence.current) return
      setMetrics(summary); setAnalyses(history.items); setRecommendations(items.items); setAudits(logs.items)
      setAnalysis(current => history.items.find(item => item.id === current?.id) ?? history.items[0] ?? null)
    } catch (value) { if (sequence === loadSequence.current) showError(value) }
    finally { if (sequence === loadSequence.current) setLoading(false) }
  }, [demo, identity, scope, auditFilter, showError])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }, [dark])

  useEffect(() => {
    if (!identity || !analysis || !busyStatuses.includes(analysis.status)) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await api.analysis(identity!.token, analysis!.id)
        if (!active) return
        setAnalysis(next)
        if (!busyStatuses.includes(next.status)) {
          if (next.recommendation_id) setSelectedId(next.recommendation_id)
          await refresh()
        } else timer = setTimeout(poll, 700)
      } catch (value) { if (active) showError(value) }
    }
    timer = setTimeout(poll, 500)
    return () => { active = false; clearTimeout(timer) }
  }, [analysis?.id, analysis?.status, identity, refresh, showError])

  async function action(name: string, operation: () => Promise<void>) {
    setBusy(name); setError(''); setNotice('')
    try { await operation() } catch (value) { showError(value) }
    finally { setBusy('') }
  }
  function startAnalysis() {
    if (!demo || !identity) return
    void action('analysis', async () => {
      const result = await api.start(identity.token, demo, scope)
      setAnalysis(result); setPage('agent'); setNotice(result.replayed ? '该分析请求已存在，返回原分析记录' : '分析已启动，正在通过工具读取经营数据')
    })
  }
  function seed() {
    if (!identity) return
    void action('seed', async () => {
      const result = await api.seed(identity.token)
      setDemo(await api.demo()); setNotice(result.message)
    })
  }
  function decide(kind: 'approve' | 'reject') {
    if (!identity || !selected) return
    void action(kind, async () => {
      await api.decision(identity.token, selected.id, kind, reason)
      setNotice(kind === 'approve' ? '审批成功，建议已批准。现在可以进行模拟执行。' : '建议已拒绝，状态和原因已记录到审计日志。')
      await refresh()
    })
  }
  function execute() {
    if (!identity || !selected) return
    void action('execute', async () => {
      try {
        const result = await api.execute(identity.token, selected.id)
        setExecution(result)
        setNotice(result.replayed ? `重复请求已命中原执行记录 ${result.execution_id}，未生成第二次模拟变更。` : '模拟执行完成，结果与审计记录已保存。')
      } catch (value) {
        if (value instanceof ApiError && value.status === 500) await refresh()
        throw value
      }
      await refresh()
    })
  }

  const activePage = pages.find(item => item.id === page)!
  const renderButton = (label = '启动 Profit Agent') => <button className="button primary" onClick={startAnalysis} disabled={!!busy || !metrics || busyStatuses.includes(analysis?.status ?? '')}>{busy === 'analysis' || busyStatuses.includes(analysis?.status ?? '') ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{label}</button>

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setPage('overview') }}><span className="brand-mark"><BarChart3 size={21} /></span><span>ProfitOps<span className="brand-ai">AI</span><small>经营决策工作空间</small></span></a>
      <div className="workspace"><span className="workspace-avatar">P</span><div>跨境增长团队<small>演示工作空间</small></div><ChevronDown size={14} /></div>
      <div className="nav-label">工作空间</div>
      <nav aria-label="主导航">{pages.map(item => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => { setPage(item.id); setError('') }} aria-current={page === item.id ? 'page' : undefined}><item.icon size={18} />{item.label}{item.id === 'approvals' && pending > 0 && <span className="nav-count">{pending}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="mode-card"><ShieldCheck size={20} /><strong>受控模拟环境</strong><p>人工审批后执行<br />全程记录，零外部操作</p><span><i />{demo?.llm?.configured ? `DeepSeek ${demo.llm.model}` : '规则引擎在线'}</span></div><div className="sidebar-foot"><span className="avatar">{isApprover ? '审' : '析'}</span><div>{identity?.name ?? '连接中'}<small>本地演示身份</small></div><button className="icon-button" aria-label={dark ? '切换浅色模式' : '切换深色模式'} onClick={() => setDark(!dark)}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button></div></div>
    </aside>

    <main>
      <header className="topbar"><div className="breadcrumb">工作空间 <span>/</span> <strong>{activePage.label}</strong></div><div className="topbar-actions"><span className="environment"><i />演示环境 · 仅模拟</span><label className="identity-select"><ShieldCheck size={14} /><select aria-label="演示身份" value={identity?.id ?? ''} onChange={event => { const next = demo?.identities.find(item => item.id === event.target.value); if (next) { setIdentity(next); setNotice(`已切换为${next.name}；权限由后端 token 校验`); setError('') } }}>{demo?.identities.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div></header>
      <div className="content">
        <div className="page-title"><div><div className="eyebrow">PROFIT INTELLIGENCE</div><h1>{activePage.label}<span className="title-dot">.</span></h1><p>{activePage.detail}</p></div><div className="title-actions"><button className="button secondary" onClick={() => { setError(''); void refresh() }} disabled={loading || !!busy}><RefreshCw size={15} className={loading ? 'spin' : ''} />刷新数据</button>{page !== 'audit' && renderButton()}</div></div>
        {error && <div className="alert error" role="alert"><TriangleAlert size={18} /><span>{error}</span><button className="icon-button" aria-label="关闭错误" onClick={() => setError('')}><X size={16} /></button></div>}
        {notice && <div className="alert notice" role="status"><CheckCircle2 size={18} /><span>{notice}</span><button className="icon-button" aria-label="关闭通知" onClick={() => setNotice('')}><X size={16} /></button></div>}
        {(page === 'overview' || page === 'agent') && <div className="filter-bar"><div><label>统计周期<select aria-label="统计周期" value={scope.days} onChange={event => setScope({ ...scope, days: Number(event.target.value) })}><option value={30}>最近 30 个数据日</option><option value={7}>最近 7 个数据日</option></select></label><label>店铺<select aria-label="店铺" value={scope.storeId} onChange={event => setScope({ ...scope, storeId: event.target.value })}><option value="">全部店铺</option>{demo?.stores.map(store => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label></div><span>{metrics ? `${metrics.period.start_date} — ${metrics.period.end_date} · UTC · USD` : '等待数据连接'}</span></div>}
        {loading && !metrics && <div className="loading" role="status"><LoaderCircle className="spin" size={22} />正在读取经营账本…</div>}
        {!loading && !demo && <Empty title="后端连接失败" text="启动后端后刷新页面，API 默认通过本机 8010 端口提供服务。"><button className="button secondary" onClick={() => window.location.reload()}>重新连接</button></Empty>}
        {demo && !demo.data_range && !loading && <Empty title="从第一份经营数据开始" text="初始化 60 天演示数据，体验一次完整的利润决策闭环。"><button className="button primary" onClick={seed} disabled={!!busy}><Database size={16} />初始化演示数据</button></Empty>}

        {page === 'overview' && metrics && <div className={loading ? 'refreshing' : ''}>
          <div className="kpi-grid">{[
            { label: '销售额', value: money(metrics.current.sales), metric: 'sales', icon: CircleDollarSign, favorable: metrics.changes.sales.absolute >= 0, sub: `${metrics.current.orders.toLocaleString()} 笔订单` },
            { label: '净利润', value: money(metrics.current.net_profit), metric: 'net_profit', icon: Activity, favorable: metrics.changes.net_profit.absolute >= 0, sub: `净销售额 ${money(metrics.current.net_sales)}` },
            { label: '净利润率', value: metrics.current.margin_pct === null ? '—' : `${metrics.current.margin_pct.toFixed(2)}%`, metric: 'margin_pct', icon: BarChart3, favorable: metrics.changes.margin_pct.absolute >= 0, sub: '净利润 / 净销售额' },
            { label: '广告支出', value: money(metrics.current.ad_spend), metric: 'ad_spend', icon: ArrowUpRight, favorable: metrics.changes.ad_spend.absolute <= 0, sub: '按每日广告账单入账' },
          ].map(item => <section className="kpi" key={item.metric}><div className="kpi-label">{item.label}<item.icon size={17} /></div><strong>{item.value}</strong><div className="kpi-bottom"><span className={`delta ${item.favorable ? 'good' : 'bad'}`}>{metrics.changes[item.metric].absolute >= 0 ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{item.metric === 'margin_pct' ? `${metrics.changes.margin_pct.absolute > 0 ? '+' : ''}${metrics.changes.margin_pct.absolute.toFixed(2)} pp` : percent(metrics.changes[item.metric].pct)}</span><span>较上一周期</span></div><p>{item.sub}</p></section>)}</div>
          {!metrics.data_quality.complete && <div className="alert error"><TriangleAlert size={18} /><div><strong>数据覆盖不足</strong><p>{metrics.data_quality.issues.join('；')}</p></div></div>}
          <section className={`insight-banner ${!metrics.anomaly ? 'healthy' : ''}`}><div className="insight-icon"><TriangleAlert size={22} /></div><div><div className="insight-top"><span className="small-label">PROFIT SIGNAL</span><span>{metrics.anomaly ? '发现经营异常' : '经营信号检查完成'}</span></div><h2>{metrics.anomaly ? '销售在增长，利润却没有跟上。' : '当前周期未发现销售与利润背离。'}</h2><p>{metrics.anomaly ? `销售额环比 ${percent(metrics.changes.sales.pct)}，净利润环比 ${percent(metrics.changes.net_profit.pct)}。${metrics.drivers[0]?.label}贡献 ${signedMoney(metrics.drivers[0]?.contribution ?? 0)}，值得优先关注。` : '可启动 Agent 核对经营数据、利润桥与完整性。'}</p></div><button className="insight-link" onClick={startAnalysis} disabled={!!busy}>分析原因<ArrowRight size={17} /></button></section>
          <div className="overview-grid"><Suspense fallback={<div className="panel chart loading">加载经营趋势…</div>}><TrendChart metrics={metrics} /></Suspense><section className="panel drivers-panel"><div className="panel-heading"><div><h2>利润变动归因</h2><p>每一项，均可回算到账本</p></div><span className="tiny-tag">USD</span></div><div className="bridge-total"><span>净利润变化</span><strong>{signedMoney(metrics.changes.net_profit.absolute)}</strong></div><div className="driver-list">{metrics.drivers.map(driver => <div className="driver-row" key={driver.metric}><div><span>{driver.label}</span><strong className={driver.contribution < 0 ? 'negative' : ''}>{signedMoney(driver.contribution)}</strong></div><div className="driver-track"><span className={driver.contribution < 0 ? 'decrease' : 'increase'} style={{ width: `${Math.max(1, Math.abs(driver.contribution) / Math.max(...metrics.drivers.map(item => Math.abs(item.contribution)), 1) * 100)}%` }} /></div></div>)}</div><div className="verified"><CheckCircle2 size={14} />{metrics.bridge_verified ? '贡献项合计与利润变化一致' : '贡献核对异常'}</div></section></div>
          <section className="panel sku-panel"><div className="panel-heading"><div><h2>商品经营表现</h2><p>找到需要优先关注的 SKU</p></div><span className="tiny-tag">{metrics.sku_breakdown.length} 个商品</span></div><div className="table-wrap"><table><thead><tr><th>商品 / SKU</th><th>销售额</th><th>净利润</th><th>利润变化</th><th>广告支出</th><th>库存 / 在途</th></tr></thead><tbody>{metrics.sku_breakdown.map(row => <tr key={row.sku}><td><strong>{row.name}</strong><small>{row.sku}</small></td><td>{money(row.current.sales)}</td><td>{money(row.current.net_profit)}</td><td className={row.profit_delta < 0 ? 'negative' : ''}>{signedMoney(row.profit_delta)}</td><td>{money(row.current.ad_spend)}</td><td>{row.inventory} <span className="muted">/ {row.inbound_inventory}</span></td></tr>)}</tbody></table></div></section>
          <div className="accounting-note"><Database size={14} /><span>净利润 = 销售额 − 折扣 − 退款 − 采购 − 广告 − 履约 − 平台费 − 其他可变成本。零分母显示“无可比基数”。</span><button className="text-button" onClick={seed} disabled={!!busy}>重新检查演示数据</button></div>
        </div>}

        {page === 'agent' && <div className="agent-layout"><section className="panel workflow-panel"><div className="panel-heading"><div><h2><Sparkles size={18} />Profit Agent</h2><p>LangGraph · {analysis?.result?.llm?.status === 'llm_success' ? `DeepSeek ${analysis.result?.llm?.model}` : demo?.llm?.configured ? `DeepSeek ${demo.llm.model} · 待调用` : '确定性规则引擎'}</p></div>{analysis && <Status value={analysis.status} />}</div>{!analysis ? <Empty title="开始一次利润诊断" text="Agent 会读取数据、计算利润、核对证据，并将建议交给你审批。">{renderButton('启动分析')}</Empty> : <><div className="run-meta"><span>RUN {analysis.id.slice(0, 8)}</span><span>{dateTime(analysis.created_at)}</span></div><div className="workflow-steps">{analysis.steps.map((step, index) => <div className={`workflow-step step-${step.status}`} key={`${step.name}-${index}`}><div className="step-node">{step.status === 'completed' ? <Check size={14} /> : step.status === 'failed' ? <X size={14} /> : <LoaderCircle className="spin" size={14} />}</div><div className="step-body"><div><strong>{step.label}</strong><span>{step.duration_ms == null ? '运行中' : `${step.duration_ms.toFixed(1)} ms`}</span></div><p>{step.summary ?? '正在执行工具…'}</p><code>{step.name}</code></div></div>)}</div>{busyStatuses.includes(analysis.status) && <div className="running-hint"><LoaderCircle className="spin" size={15} />正在通过后端工作流处理…</div>}{analysis.error && <div className="alert error">{analysis.error.code}：{analysis.error.message}</div>}<div className="run-footer"><Fingerprint size={14} /><span>请求 ID：{analysis.request_id}</span></div></>}
          {analyses.length > 1 && <label className="history-picker">查看历史分析<select aria-label="历史分析" value={analysis?.id ?? ''} onChange={event => { const value = analyses.find(item => item.id === event.target.value); if (value) setAnalysis(value) }}>{analyses.map(item => <option value={item.id} key={item.id}>{item.id.slice(0, 8)} · {statusLabels[item.status]} · {dateTime(item.created_at)}</option>)}</select></label>}
        </section><div className="agent-result"><section className="panel"><div className="panel-heading"><div><h2>分析结论与依据</h2><p>计算结果绑定统计周期与数据来源</p></div><Terminal size={18} /></div>{analysis?.result ? <><div className="conclusion"><span className="small-label">AGENT FINDING</span><h3>{analysis.result.findings?.conclusion}</h3><p>当前：{analysis.result.metrics.period.start_date} — {analysis.result.metrics.period.end_date}<br />上一周期：{analysis.result.metrics.previous_period.start_date} — {analysis.result.metrics.previous_period.end_date}</p></div><div className="evidence-stats"><div><span>净利润变化</span><strong>{signedMoney(analysis.result.metrics.changes.net_profit.absolute)}</strong></div><div><span>数据覆盖</span><strong>{analysis.result.metrics.data_quality.observed_records} / {analysis.result.metrics.data_quality.expected_records}</strong></div></div><div className="evidence-list">{analysis.result.findings?.evidence.slice(0, 4).map(item => <div key={item.id}><span>{item.label}<small>{item.source}</small></span><strong>{signedMoney(item.contribution)}</strong></div>)}</div>{analysis.recommendation_id && <button className="button primary full" onClick={() => { setSelectedId(analysis.recommendation_id!); setPage('approvals') }}>查看经营建议<ArrowRight size={16} /></button>}</> : <Empty title="等待分析结果" text="完成计算与证据核验后，将在这里展示结论。" />}</section><LLMPanel llm={analysis?.result?.llm} config={demo?.llm} /><section className="explain-card"><ShieldCheck size={19} /><div><h3>让数据决定结论，让人决定行动</h3><p>不完整的数据会暂停建议生成。建议必须经人工审批；模拟只产生数据库预览记录，真实广告和资金均不变。</p><span>当前引擎：规则与本地工具 · 无模型调用</span></div></section></div></div>}

        {page === 'approvals' && <div className="approval-layout"><section className="panel recommendation-list"><div className="panel-heading"><div><h2>经营建议</h2><p>{pending} 项等待人工复核</p></div><span className="count-circle">{recommendations.length}</span></div>{!recommendations.length ? <Empty title="还没有待处理建议" text="先启动 Profit Agent，异常分析通过证据核验后会生成建议。" /> : recommendations.map(item => <button key={item.id} className={`recommendation-item ${selected?.id === item.id ? 'selected' : ''}`} onClick={() => { setSelectedId(item.id); setExecution(null); setNotice('') }}><Status value={item.status} /><strong>{item.title}</strong><p>{item.body.recommendation}</p><small>{dateTime(item.created_at)}</small></button>)}</section>{selected ? <section className="panel recommendation-detail"><div className="panel-heading"><div><span className="small-label">RECOMMENDATION / {selected.id.slice(0, 8)}</span><h2>{selected.title}</h2></div><Status value={selected.status} /></div><div className="detail-section"><h3>问题与建议</h3><p>{selected.body.problem}</p><div className="recommendation-callout"><Sparkles size={19} /><strong>{selected.body.recommendation}</strong></div></div><div className="detail-section"><h3>数据证据 <span className="tiny-tag">可核对</span></h3><p className="muted">{selected.body.period.start_date} — {selected.body.period.end_date} 对比 {selected.body.previous_period.start_date} — {selected.body.previous_period.end_date} · {selected.body.store_id ?? '全部店铺'} · USD</p><div className="table-wrap"><table><thead><tr><th>指标 / 数据来源</th><th>上一周期</th><th>当前周期</th><th>利润贡献</th></tr></thead><tbody>{selected.body.evidence.map(item => <tr key={item.id}><td>{item.label}<small>{item.source}</small></td><td>{money(item.previous)}</td><td>{money(item.current)}</td><td>{signedMoney(item.contribution)}</td></tr>)}</tbody></table></div></div>{selected.body.llm && <div className="detail-section"><h3>LLM 根因分析 <span className="tiny-tag">{llmStatusLabels[selected.body.llm.status] ?? selected.body.llm.status}</span></h3>{selected.body.llm.output ? <><p>{selected.body.llm.output.summary}</p><div className="llm-refs">{selected.body.llm.output.evidence_references.map(id => <code key={id}>{id}</code>)}</div><ul className="llm-cause-list">{selected.body.llm.output.root_causes.map(cause => <li key={cause.cause}><strong>{cause.cause}</strong><span>{cause.reasoning}</span></li>)}</ul></> : <p className="muted">本次建议由确定性规则生成，LLM 未参与（{llmReasonLabels[selected.body.llm.reason ?? ''] ?? selected.body.llm.reason ?? '未调用'}）。</p>}</div>}<div className="impact-box"><div><span>条件性利润改善情景</span><strong>{money(selected.body.expected_impact.profit_improvement_min)} — {money(selected.body.expected_impact.profit_improvement_max)}</strong><small>未来 {selected.body.expected_impact.horizon_days} 天 · 非实际收益或保证值</small></div><p>{selected.body.expected_impact.explanation}</p></div><div className="detail-section"><h3>待执行的模拟动作 <span className="tiny-tag">SIMULATION ONLY</span></h3>{selected.action.type === 'adjust_ad_budget' ? <div className="budget-preview"><div><span>参考日预算</span><strong>{money(selected.action.baseline_daily_budget)}</strong></div><ArrowRight size={18} /><div><span>模拟新预算</span><strong>{money(selected.action.proposed_daily_budget)}</strong></div></div> : <p>为 {selected.action.sku} 创建成本核查任务预览。</p>}<p className="muted">目标 SKU：{selected.action.sku}。该动作只写入模拟记录，不会调用广告、采购或支付系统。</p></div><div className="detail-columns"><div><h3><TriangleAlert size={16} />风险与假设</h3><ul>{[...selected.body.risks, ...selected.body.assumptions].map(value => <li key={value}>{value}</li>)}</ul></div><div><h3><Database size={16} />待补充信息</h3><ul>{selected.body.missing_information.map(value => <li key={value}>{value}</li>)}</ul></div></div><div className="approval-actions"><div className="approval-title"><ShieldCheck size={18} /><strong>人工审批与模拟执行</strong><span className="tiny-tag">SIMULATION ONLY</span></div>{!isApprover && <p className="muted">当前为经营分析师。请在右上角切换“审批负责人”进行审批与模拟执行。</p>}{selected.status === 'pending_approval' && isApprover && <><label className="reason-label">审批意见<textarea aria-label="审批意见" value={reason} onChange={event => setReason(event.target.value)} rows={2} /></label><div className="action-row"><button className="button danger" onClick={() => decide('reject')} disabled={!!busy || reason.trim().length < 2}><X size={15} />拒绝建议</button><button className="button primary" onClick={() => decide('approve')} disabled={!!busy || reason.trim().length < 2}><Check size={15} />批准建议</button></div></>}{selected.approval_reason && <p className="decision-note">审批意见：{selected.approval_reason}</p>}{isApprover && ['approved', 'executed', 'execution_failed'].includes(selected.status) && <button className="button primary" onClick={execute} disabled={!!busy}>{busy === 'execute' ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{selected.status === 'approved' ? '模拟执行已批准动作' : '再次请求 · 验证幂等'}</button>}<div className="state-flow">{['pending_approval', 'approved', 'executing', 'executed'].map((value, index) => <span key={value} className={selected.status === value ? 'current' : ''}>{index > 0 && <ArrowRight size={12} />}{statusLabels[value]}</span>)}</div></div>{(selected.execution || execution?.recommendation_id === selected.id) && <ExecutionResult result={execution?.recommendation_id === selected.id ? execution : selected.execution!} />}</section> : <section className="panel"><Empty title="选择一条建议" text="建议会展示统计范围、证据、影响情景与审批状态。" /></section>}</div>}

        {page === 'audit' && <section className="panel audit-panel"><div className="panel-heading"><div><h2>决策与执行轨迹</h2><p>最近 {audits.length} 条事件 · 记录批准、拒绝、模拟、失败与重复请求</p></div><span className="tiny-tag"><Fingerprint size={13} />请求级追踪</span></div><form className="filter-bar audit-filter" onSubmit={event => { event.preventDefault(); setAuditFilter(auditQuery.trim()) }}><div><label>请求 ID<input aria-label="按请求 ID 过滤" placeholder="精确匹配请求 ID" value={auditQuery} onChange={event => setAuditQuery(event.target.value)} /></label><button className="button secondary" type="submit">筛选</button>{auditFilter && <button className="text-button" type="button" onClick={() => { setAuditQuery(''); setAuditFilter('') }}>清除筛选</button>}</div><span>{auditFilter ? `当前筛选：${auditFilter}` : '显示最近 100 条；可按请求 ID 精确追踪'}</span></form>{!audits.length ? <Empty title={auditFilter ? '没有匹配的事件' : '审计日志为空'} text={auditFilter ? `请求 ID ${auditFilter} 没有对应审计事件，请检查后重试。` : '启动一次分析，系统会自动记录后续决策与执行事件。'} /> : <div className="table-wrap"><table><thead><tr><th>时间 / 本地显示</th><th>操作人</th><th>动作</th><th>对象</th><th>结果</th><th>关联请求 ID</th></tr></thead><tbody>{audits.map(item => <tr key={item.id}><td>{dateTime(item.created_at)}</td><td><span className="actor-cell">{item.actor === 'demo-approver' ? <ShieldCheck size={14} /> : <Activity size={14} />}{item.actor}</span></td><td><strong>{item.action}</strong><details><summary>事件详情</summary><pre>{JSON.stringify(item.details, null, 2)}</pre></details></td><td><span className="mono" title={item.object_id}>{item.object_id.slice(0, 12)}</span><small>{item.object_type}</small></td><td><span className={`audit-outcome ${item.outcome}`}>{item.outcome === 'success' ? '✓ 成功' : item.outcome === 'replayed' ? '↻ 幂等返回' : item.outcome === 'denied' ? '⊘ 已拦截' : item.outcome === 'degraded' ? '⚠ 已降级' : item.outcome === 'failure' ? '× 失败' : item.outcome}</span></td><td><code className="request-id">{item.request_id}</code></td></tr>)}</tbody></table></div>}</section>}
        <footer className="page-footer"><span>ProfitOps AI <span className="muted">/</span> 从数据到决策的完整闭环</span><span><ShieldCheck size={12} />所有执行均为模拟 · {demo?.data_range ? '数据已接入' : '等待初始化'}</span></footer>
      </div>
    </main>
  </div>
}

function LLMPanel({ llm, config }: { llm?: LLMRun | null; config?: LLMConfig }) {
  const status = llm?.status
  const output = llm?.output
  return <section className="panel llm-panel">
    <div className="panel-heading"><div><h2><Sparkles size={17} />DeepSeek 根因分析</h2><p>{config?.configured ? `真实模型 ${config.model}` : '未配置 API Key · 确定性规则'}</p></div><span className={`llm-badge ${status ?? 'pending'}`}>{status ? llmStatusLabels[status] ?? status : '等待分析'}</span></div>
    {!llm ? <p className="llm-empty">启动一次异常分析后，这里会显示模型解释、证据引用与经营建议。{config?.configured ? '当前已配置 DeepSeek。' : '当前未配置 LLM，将使用确定性规则。'}</p> : <>
      <div className="llm-meta">
        <span>模型<strong>{llm.model}</strong></span>
        <span>耗时<strong>{llm.latency_ms == null ? '—' : `${llm.latency_ms.toFixed(1)} ms`}</strong></span>
        {llm.usage && <span>Token<strong>{llm.usage.total_tokens ?? '—'}{llm.usage.prompt_tokens != null && <small>输入 {llm.usage.prompt_tokens} / 输出 {llm.usage.completion_tokens ?? '—'}</small>}</strong></span>}
        <span>尝试<strong>{llm.attempts}</strong></span>
      </div>
      {output ? <>
        <div className="llm-summary"><span className="small-label">LLM SUMMARY</span><p>{output.summary}</p><span className="llm-confidence">置信度 {(output.confidence * 100).toFixed(0)}%</span></div>
        <div className="llm-section"><h3>根因分析</h3>{output.root_causes.map(cause => <div className="llm-cause" key={cause.cause}><strong>{cause.cause}</strong><p>{cause.reasoning}</p><div className="llm-refs">{cause.evidence_ids.map(id => <code key={id}>{id}</code>)}</div></div>)}</div>
        <div className="llm-section"><h3>经营建议</h3>{output.recommendations.map(item => <div className="llm-recommendation" key={item.action}><div><strong>{item.action}</strong><span className={`llm-priority ${item.priority}`}>{item.priority}</span></div><p>{item.rationale}</p><div className="llm-refs">{item.evidence_ids.map(id => <code key={id}>{id}</code>)}</div></div>)}</div>
        <div className="llm-section"><h3>预期影响</h3><p>{output.expected_impact.explanation}</p><span className="muted">{output.expected_impact.horizon_days} 天 · 条件情景，不是收益承诺</span></div>
        {(output.risks.length > 0 || output.missing_information.length > 0) && <div className="llm-section"><h3>风险与待补充</h3><ul className="llm-notes">{output.risks.map(value => <li key={value}>{value}</li>)}{output.missing_information.map(value => <li key={value}>{value}</li>)}</ul></div>}
        <div className="llm-section"><h3>证据引用</h3><div className="llm-refs">{output.evidence_references.map(id => <code key={id}>{id}</code>)}</div></div>
      </> : <div className="llm-fallback"><p><strong>{status ? llmStatusLabels[status] ?? status : 'LLM 未参与'}</strong>{llm.reason ? `：${llmReasonLabels[llm.reason] ?? llm.reason}` : ''}</p>{llm.error && <p className="muted">错误码 {llm.error.code} · {llm.error.message}</p>}<p className="muted">{status === 'not_requested' ? '本次分析未调用 LLM；展示的是确定性计算结果。' : '本次结论、证据与建议由确定性规则引擎生成，不是 LLM 输出。'}</p></div>}
    </>}
  </section>
}

function ExecutionResult({ result }: { result: Execution }) {
  return <div className={`execution-result ${result.status === 'execution_failed' ? 'failure' : ''}`}><div><CheckCircle2 size={20} /><h3>{result.status === 'execution_failed' ? '模拟执行失败' : '模拟执行结果'}<span className="tiny-tag">SIMULATION</span></h3></div><p>{result.error?.message ?? result.result?.message}</p>{result.result?.before && <div className="budget-preview"><div><span>参考日预算</span><strong>{money(result.result.before.daily_budget)}</strong></div><ArrowRight size={18} /><div><span>模拟新预算</span><strong>{money(result.result.after!.daily_budget)}</strong></div></div>}<p className="mono">执行 ID：{result.execution_id}</p><div className="result-foot"><span><Check size={13} />外部调用 {result.result?.external_calls ?? 0} 次</span><span><Fingerprint size={13} />{result.replayed ? '命中幂等，复用原执行记录' : '唯一执行记录已保存'}</span></div></div>
}

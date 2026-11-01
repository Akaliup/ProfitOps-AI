import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, BookOpen, Check, CheckCircle2, ChevronDown, CircleDollarSign, Clock3, Database, FileCheck2, Fingerprint, LayoutDashboard, LoaderCircle, Moon, Play, RefreshCw, Search, ShieldCheck, Sparkles, Sun, Terminal, TriangleAlert, X } from 'lucide-react'
import { api, ApiError } from './api'
import type { Analysis, Audit, Demo, Execution, Identity, KnowledgeContext, KnowledgeDocument, KnowledgeSearch, LLMConfig, LLMRun, Metrics, OpsSummary, Recommendation, Scope } from './types'
import { dateTime, llmReasonLabels, llmStatusLabels, money, percent, signedMoney, statusLabels, terminationLabels } from './format'
import './styles.css'

const TrendChart = lazy(() => import('./components/TrendChart').then(module => ({ default: module.TrendChart })))

type Page = 'overview' | 'agent' | 'approvals' | 'knowledge' | 'audit' | 'ops'
const pages = [
  { id: 'overview' as const, label: '经营总览', icon: LayoutDashboard, detail: '把增长，变成利润。' },
  { id: 'agent' as const, label: 'Agent 工作台', icon: Sparkles, detail: '从经营信号，到有据可依的行动。' },
  { id: 'approvals' as const, label: '审批与执行', icon: FileCheck2, detail: '每一步行动，都由你掌控。' },
  { id: 'ops' as const, label: 'Agent 运维', icon: Activity, detail: '任务、模型与成本的运行视图。' },
  { id: 'knowledge' as const, label: '运营知识库', icon: BookOpen, detail: '让建议有规则可依，让引用可追溯。' },
  { id: 'audit' as const, label: '审计日志', icon: Fingerprint, detail: '让每一次决策都有迹可循。' },
]
const busyStatuses = ['queued', 'running']
const usd = (value: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(value)
const countText = (value: number | null) => value == null ? '暂无数据' : value.toLocaleString('zh-CN')

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
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [ops, setOps] = useState<OpsSummary | null>(null)
  const [opsHours, setOpsHours] = useState(24)
  const [opsLoading, setOpsLoading] = useState(false)
  const [knowledgeQuery, setKnowledgeQuery] = useState('')
  const [knowledgeResults, setKnowledgeResults] = useState<KnowledgeSearch | null>(null)
  const [knowledgeDraft, setKnowledgeDraft] = useState({ title: '', source: 'manual://profitops/knowledge', version: 'v1', content: '' })
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
      const [summary, history, items, logs, knowledgeDocs] = await Promise.all([
        demo.data_range ? api.summary(identity.token, demo, scope) : Promise.resolve(null),
        api.analyses(identity.token), api.recommendations(identity.token), api.audits(identity.token, auditFilter),
        api.knowledgeDocuments(identity.token),
      ])
      if (sequence !== loadSequence.current) return
      setMetrics(summary); setAnalyses(history.items); setRecommendations(items.items); setAudits(logs.items)
      setDocuments(knowledgeDocs.items)
      setAnalysis(current => history.items.find(item => item.id === current?.id) ?? history.items[0] ?? null)
    } catch (value) { if (sequence === loadSequence.current) showError(value) }
    finally { if (sequence === loadSequence.current) setLoading(false) }
  }, [demo, identity, scope, auditFilter, showError])

  const loadOps = useCallback(async () => {
    if (!identity || identity.role !== 'approver') return
    setOpsLoading(true); setError('')
    try { setOps(await api.ops(identity.token, opsHours)) }
    catch (value) { showError(value) }
    finally { setOpsLoading(false) }
  }, [identity, opsHours, showError])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (page === 'ops' && isApprover) void loadOps()
  }, [page, isApprover, loadOps])
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
  function retryAnalysis() {
    if (!identity || !analysis) return
    void action('analysis-retry', async () => {
      const result = await api.retryAnalysis(identity.token, analysis.id)
      setAnalysis(result)
      setNotice('失败任务已重新排队，工作流会从新的一次尝试开始。')
    })
  }
  function openAnalysis(id: string) {
    if (!identity) return
    void action('ops-trace', async () => {
      const value = await api.analysis(identity.token, id)
      setAnalysis(value)
      if (value.recommendation_id) setSelectedId(value.recommendation_id)
      setPage('agent')
      setNotice(`已打开分析 ${id.slice(0, 8)} 的执行轨迹。`)
    })
  }
  function seed() {
    if (!identity) return
    void action('seed', async () => {
      const result = await api.seed(identity.token)
      setDemo(await api.demo()); setNotice(result.message)
    })
  }
  function searchKnowledge(event: React.FormEvent) {
    event.preventDefault()
    if (!identity || knowledgeQuery.trim().length < 2) return
    void action('knowledge-search', async () => {
      const result = await api.knowledgeSearch(identity.token, knowledgeQuery.trim())
      setKnowledgeResults(result)
      setNotice(result.no_answer ? '知识库没有达到阈值的可靠片段。' : `检索到 ${result.hits.length} 个知识片段。`)
    })
  }
  function addKnowledgeDocument(event: React.FormEvent) {
    event.preventDefault()
    if (!identity || knowledgeDraft.title.trim().length < 2 || knowledgeDraft.content.trim().length < 10) return
    void action('knowledge-create', async () => {
      const result = await api.createKnowledgeDocument(identity.token, {
        title: knowledgeDraft.title.trim(), source: knowledgeDraft.source.trim(),
        version: knowledgeDraft.version.trim() || 'v1', content: knowledgeDraft.content,
      })
      setNotice(result.created ? `知识文档已创建，共 ${result.chunk_count} 个片段。` : '内容相同的文档已存在，未重复创建。')
      setKnowledgeDraft({ title: '', source: 'manual://profitops/knowledge', version: 'v1', content: '' })
      const [docs, nextDemo] = await Promise.all([api.knowledgeDocuments(identity.token), api.demo()])
      setDocuments(docs.items); setDemo(nextDemo)
    })
  }
  function removeKnowledgeDocument(id: string) {
    if (!identity) return
    void action('knowledge-delete', async () => {
      await api.deleteKnowledgeDocument(identity.token, id)
      setNotice('知识文档已停用，历史审批与审计记录不受影响。')
      const [docs, nextDemo] = await Promise.all([api.knowledgeDocuments(identity.token), api.demo()])
      setDocuments(docs.items); setDemo(nextDemo)
      setKnowledgeResults(current => current ? { ...current, hits: current.hits.filter(hit => hit.document_id !== id) } : current)
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
        <div className="page-title"><div><div className="eyebrow">PROFIT INTELLIGENCE</div><h1>{activePage.label}<span className="title-dot">.</span></h1><p>{activePage.detail}</p></div><div className="title-actions"><button className="button secondary" onClick={() => { setError(''); if (page === 'ops') void loadOps(); else void refresh() }} disabled={loading || opsLoading || !!busy}><RefreshCw size={15} className={loading || opsLoading ? 'spin' : ''} />刷新数据</button>{!['audit', 'knowledge', 'ops'].includes(page) && renderButton()}</div></div>
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

        {page === 'agent' && <div className="agent-layout"><section className="panel workflow-panel"><div className="panel-heading"><div><h2><Sparkles size={18} />Profit Agent</h2><p>LangGraph · {analysis?.result?.llm?.status === 'llm_success' ? `DeepSeek ${analysis.result?.llm?.model}` : demo?.llm?.configured ? `DeepSeek ${demo.llm.model} · 待调用` : '确定性规则引擎'}</p></div>{analysis && <Status value={analysis.status} />}</div>{!analysis ? <Empty title="开始一次利润诊断" text="Agent 会读取数据、计算利润、核对证据，并将建议交给你审批。">{renderButton('启动分析')}</Empty> : <><div className="run-meta"><span>RUN {analysis.id.slice(0, 8)}</span><span>尝试 {analysis.attempts}/{analysis.max_attempts}</span><span>{dateTime(analysis.created_at)}</span>{analysis.next_run_at && <span>下次重试 {dateTime(analysis.next_run_at)}</span>}</div><div className="workflow-steps">{analysis.steps.map((step, index) => <div className={`workflow-step step-${step.status}`} key={`${step.name}-${index}`}><div className="step-node">{step.status === 'completed' ? <Check size={14} /> : step.status === 'failed' ? <X size={14} /> : <LoaderCircle className="spin" size={14} />}</div><div className="step-body"><div><strong>{step.label}</strong><span>{step.duration_ms == null ? '运行中' : `${step.duration_ms.toFixed(1)} ms`}</span></div><p>{step.summary ?? '正在执行工具…'}</p><code>{step.name}</code></div></div>)}</div>{busyStatuses.includes(analysis.status) && <div className="running-hint"><LoaderCircle className="spin" size={15} />正在通过后端工作流处理…</div>}{analysis.error && <div className="alert error">{analysis.error.code}：{analysis.error.message}</div>}{analysis.status === 'failed' && <button className="button secondary" onClick={retryAnalysis} disabled={!!busy}>{busy === 'analysis-retry' ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重新排队执行</button>}<div className="run-footer"><Fingerprint size={14} /><span>请求 ID：{analysis.request_id}</span></div></>}
          {analyses.length > 1 && <label className="history-picker">查看历史分析<select aria-label="历史分析" value={analysis?.id ?? ''} onChange={event => { const value = analyses.find(item => item.id === event.target.value); if (value) setAnalysis(value) }}>{analyses.map(item => <option value={item.id} key={item.id}>{item.id.slice(0, 8)} · {statusLabels[item.status]} · {dateTime(item.created_at)}</option>)}</select></label>}
        </section><div className="agent-result"><section className="panel"><div className="panel-heading"><div><h2>分析结论与依据</h2><p>计算结果绑定统计周期与数据来源</p></div><Terminal size={18} /></div>{analysis?.result ? <><div className="conclusion"><span className="small-label">AGENT FINDING</span><h3>{analysis.result.findings?.conclusion}</h3><p>当前：{analysis.result.metrics.period.start_date} — {analysis.result.metrics.period.end_date}<br />上一周期：{analysis.result.metrics.previous_period.start_date} — {analysis.result.metrics.previous_period.end_date}</p></div><div className="evidence-stats"><div><span>净利润变化</span><strong>{signedMoney(analysis.result.metrics.changes.net_profit.absolute)}</strong></div><div><span>数据覆盖</span><strong>{analysis.result.metrics.data_quality.observed_records} / {analysis.result.metrics.data_quality.expected_records}</strong></div></div><div className="evidence-list">{analysis.result.findings?.evidence.slice(0, 4).map(item => <div key={item.id}><span>{item.label}<small>{item.source}</small></span><strong>{signedMoney(item.contribution)}</strong></div>)}</div>{analysis.recommendation_id && <button className="button primary full" onClick={() => { setSelectedId(analysis.recommendation_id!); setPage('approvals') }}>查看经营建议<ArrowRight size={16} /></button>}</> : <Empty title="等待分析结果" text="完成计算与证据核验后，将在这里展示结论。" />}</section><LLMPanel llm={analysis?.result?.llm} config={demo?.llm} /><KnowledgePanel knowledge={analysis?.result?.knowledge} /><AgentTrace llm={analysis?.result?.llm} /><section className="explain-card"><ShieldCheck size={19} /><div><h3>让数据决定结论，让人决定行动</h3><p>不完整的数据会暂停建议生成。建议必须经人工审批；模拟只产生数据库预览记录，真实广告和资金均不变。</p><span>当前引擎：规则与本地工具 · 无模型调用</span></div></section></div></div>}

        {page === 'ops' && <div className="ops-page">
          {!isApprover ? <Empty title="需要审批负责人权限" text="Agent 运维视图包含跨任务的失败详情与模型成本，仅审批负责人可查看。请使用右上角切换为审批负责人。" /> : <>
            <div className="ops-toolbar"><div><label>统计窗口<select aria-label="运维统计窗口" value={opsHours} onChange={event => setOpsHours(Number(event.target.value))}><option value={24}>最近 24 小时</option><option value={72}>最近 3 天</option><option value={168}>最近 7 天</option></select></label></div><span>{ops ? `${ops.window.start} — ${ops.window.end} · UTC · 持久化记录` : '等待持久化记录'}</span></div>
            {opsLoading && !ops && <div className="loading" role="status"><LoaderCircle className="spin" size={22} />正在聚合任务、模型轨迹与审计记录…</div>}
            {!opsLoading && !ops && <Empty title="暂无运维数据" text="当前窗口没有可聚合的任务记录。启动一次分析后，任务、工具调用与成本会显示在这里。" />}
            {ops && <>
              <div className="ops-kpi-grid">
                <section className="ops-kpi"><span>任务总数</span><strong>{ops.jobs.total}</strong><p>排队 {ops.jobs.by_status.queued ?? 0} · 执行中 {ops.jobs.by_status.running ?? 0} · 待审批 {ops.jobs.by_status.waiting_approval ?? 0}</p></section>
                <section className="ops-kpi"><span>失败任务</span><strong className={ops.jobs.by_status.failed ? 'negative' : ''}>{ops.jobs.by_status.failed ?? 0}</strong><p>死信 {ops.jobs.dead_letter} · 重试 {ops.jobs.retry_count} 次</p></section>
                <section className="ops-kpi"><span>LLM 运行</span><strong>{ops.llm.runs}</strong><p>模型调用 {ops.llm.model_calls} · 无模型调用 {ops.llm.no_model_call_runs} · 成功 {ops.llm.by_status.llm_success ?? 0} · 降级 {ops.llm.by_status.deterministic_fallback ?? 0} · 失败 {ops.llm.by_status.llm_failed ?? 0}</p></section>
                <section className="ops-kpi"><span>估算成本</span><strong>{ops.cost.status === 'complete' ? usd(ops.cost.amount_usd ?? 0) : ops.cost.status === 'partial' ? '部分未知' : '暂无数据'}</strong><p>{ops.cost.status === 'complete' ? `${ops.cost.priced_runs} 次调用 · ${ops.cost.pricing_version}` : ops.cost.status === 'partial' ? `${ops.cost.unpriced_runs} 次缺少 Usage 或价格${ops.cost.known_subtotal_usd != null ? ` · 已知小计 ${usd(ops.cost.known_subtotal_usd)}` : ''}` : '没有可定价的模型调用'}</p></section>
              </div>
              <div className="ops-grid">
                <section className="panel ops-panel">
                  <div className="panel-heading"><div><h2>任务队列</h2><p>按窗口内创建的任务聚合</p></div><span className="tiny-tag">{ops.window.hours}H</span></div>
                  <div className="ops-rows">
                    {['queued', 'running', 'waiting_approval', 'completed', 'insufficient_data', 'failed'].map(status => <div className="ops-row" key={status}><span>{statusLabels[status] ?? status}</span><strong>{ops.jobs.by_status[status] ?? 0}</strong></div>)}
                    <div className="ops-row emphasis"><span>死信任务</span><strong>{ops.jobs.dead_letter}</strong></div>
                    <div className="ops-row"><span>重试次数（按 attempts）</span><strong>{ops.jobs.retry_count}</strong></div>
                    <div className="ops-row"><span>重试调度事件</span><strong>{ops.jobs.retry_scheduled_events}</strong></div>
                  </div>
                </section>
                <section className="panel ops-panel">
                  <div className="panel-heading"><div><h2>模型与工具</h2><p>来自已持久化的 LLM 运行与工具轨迹</p></div><Terminal size={18} /></div>
                  <div className="ops-rows">
                    {Object.entries(ops.llm.by_status).map(([status, count]) => <div className="ops-row" key={status}><span>{llmStatusLabels[status] ?? status}</span><strong>{count}</strong></div>)}
                    <div className="ops-row"><span>原生工具运行</span><strong>{ops.llm.native_tool_runs}</strong></div>
                    <div className="ops-row"><span>工具调用 / 失败</span><strong>{ops.tools.calls} / {ops.tools.failed_calls}</strong></div>
                    <div className="ops-row"><span>工具轮次</span><strong>{ops.tools.rounds}</strong></div>
                  </div>
                  {ops.tools.by_name.length > 0 && <div className="ops-chips">{ops.tools.by_name.map(item => <span key={item.name}><code>{item.name}</code>{item.calls} 次{item.failed ? ` · ${item.failed} 失败` : ''}</span>)}</div>}
                  {ops.llm.termination_reasons.length > 0 && <p className="ops-note">终止原因：{ops.llm.termination_reasons.map(item => `${terminationLabels[item.reason] ?? item.reason} ×${item.count}`).join('；')}</p>}
                </section>
                <section className="panel ops-panel">
                  <div className="panel-heading"><div><h2>Token 与成本</h2><p>缺失用量或价格时显示未知，不按 0 计</p></div><CircleDollarSign size={18} /></div>
                  <div className="ops-token-grid">
                    <div><span>输入 Token</span><strong>{countText(ops.tokens.prompt)}</strong></div>
                    <div><span>输出 Token</span><strong>{countText(ops.tokens.completion)}</strong></div>
                    <div><span>总 Token</span><strong>{countText(ops.tokens.total)}</strong></div>
                    <div><span>缓存命中</span><strong>{countText(ops.tokens.cached)}</strong></div>
                  </div>
                  <p className="ops-note">{ops.tokens.runs_with_usage} 次运行有 Usage{ops.tokens.runs_without_usage ? `，${ops.tokens.runs_without_usage} 次缺失` : ''}。</p>
                  <div className={`ops-cost ${ops.cost.status}`}><span>估算成本 · {ops.cost.currency}</span><strong>{ops.cost.status === 'complete' ? usd(ops.cost.amount_usd ?? 0) : ops.cost.status === 'partial' ? '未知' : '暂无数据'}</strong><p>{ops.cost.status === 'complete' ? `${ops.cost.priced_runs} 次调用 · 单价版本 ${ops.cost.pricing_version} · 公开列表价估算，非账单` : ops.cost.status === 'partial' ? `${ops.cost.unpriced_runs} 次调用缺少 Usage 或价格，未按 0 计。${ops.cost.known_subtotal_usd != null ? ` 已知小计 ${usd(ops.cost.known_subtotal_usd)}，仅作诊断，不是总成本。` : ''}` : '没有可定价的模型调用记录。'}</p></div>
                </section>
                <section className="panel ops-panel">
                  <div className="panel-heading"><div><h2>错误与人工环节</h2><p>错误码来自失败任务；审批与执行展示当前状态</p></div><TriangleAlert size={18} /></div>
                  {!ops.jobs.error_codes.length ? <p className="ops-note">窗口内没有失败错误码。</p> : <div className="ops-chips">{ops.jobs.error_codes.map(item => <span key={item.code}><code>{item.code}</code>{item.count} 次</span>)}</div>}
                  <div className="ops-rows">
                    <div className="ops-row"><span>待审批建议</span><strong>{ops.approvals.by_status.pending_approval ?? 0}</strong></div>
                    <div className="ops-row"><span>已批准建议</span><strong>{ops.approvals.by_status.approved ?? 0}</strong></div>
                    <div className="ops-row"><span>已执行建议</span><strong>{ops.approvals.by_status.executed ?? 0}</strong></div>
                    <div className="ops-row"><span>模拟执行记录</span><strong>{ops.executions.total}</strong></div>
                  </div>
                </section>
              </div>
              <section className="panel ops-failures">
                <div className="panel-heading"><div><h2>最近失败任务</h2><p>点击查看该分析已保存的执行轨迹</p></div><span className="tiny-tag">{ops.recent_failures.length}</span></div>
                {!ops.recent_failures.length ? <Empty title="暂无失败任务" text="当前窗口没有失败记录。任务失败或进入死信后会显示在这里。" /> : <div className="table-wrap"><table><thead><tr><th>任务 / 请求 ID</th><th>错误</th><th>尝试</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{ops.recent_failures.map(item => <tr key={item.id}><td><strong>{item.id.slice(0, 8)}</strong><small className="mono">{item.request_id}</small></td><td>{item.dead_letter && <span className="status status-failed">死信</span>}<small>{item.error_code ?? 'unknown'}：{item.error_message ?? '无错误详情'}</small></td><td>{item.attempts} / {item.max_attempts}</td><td>{dateTime(item.updated_at)}</td><td><button className="text-button" onClick={() => openAnalysis(item.id)} disabled={!!busy}>查看 Trace</button></td></tr>)}</tbody></table></div>}
              </section>
              <div className="accounting-note"><Database size={14} /><span>{ops.source.note} {ops.notes.join(' ')}</span></div>
            </>}
          </>}
        </div>}

        {page === 'approvals' && <div className="approval-layout"><section className="panel recommendation-list"><div className="panel-heading"><div><h2>经营建议</h2><p>{pending} 项等待人工复核</p></div><span className="count-circle">{recommendations.length}</span></div>{!recommendations.length ? <Empty title="还没有待处理建议" text="先启动 Profit Agent，异常分析通过证据核验后会生成建议。" /> : recommendations.map(item => <button key={item.id} className={`recommendation-item ${selected?.id === item.id ? 'selected' : ''}`} onClick={() => { setSelectedId(item.id); setExecution(null); setNotice('') }}><Status value={item.status} /><strong>{item.title}</strong><p>{item.body.recommendation}</p><small>{dateTime(item.created_at)}</small></button>)}</section>{selected ? <section className="panel recommendation-detail"><div className="panel-heading"><div><span className="small-label">RECOMMENDATION / {selected.id.slice(0, 8)}</span><h2>{selected.title}</h2></div><Status value={selected.status} /></div><div className="detail-section"><h3>问题与建议</h3><p>{selected.body.problem}</p><div className="recommendation-callout"><Sparkles size={19} /><strong>{selected.body.recommendation}</strong></div></div><div className="detail-section"><h3>数据证据 <span className="tiny-tag">可核对</span></h3><p className="muted">{selected.body.period.start_date} — {selected.body.period.end_date} 对比 {selected.body.previous_period.start_date} — {selected.body.previous_period.end_date} · {selected.body.store_id ?? '全部店铺'} · USD</p><div className="table-wrap"><table><thead><tr><th>指标 / 数据来源</th><th>上一周期</th><th>当前周期</th><th>利润贡献</th></tr></thead><tbody>{selected.body.evidence.map(item => <tr key={item.id}><td>{item.label}<small>{item.source}</small></td><td>{money(item.previous)}</td><td>{money(item.current)}</td><td>{signedMoney(item.contribution)}</td></tr>)}</tbody></table></div></div><KnowledgePanel knowledge={selected.body.knowledge} showCited />{selected.body.llm && <div className="detail-section"><h3>LLM 根因分析 <span className="tiny-tag">{llmStatusLabels[selected.body.llm.status] ?? selected.body.llm.status}</span></h3>{selected.body.llm.output ? <><p>{selected.body.llm.output.summary}</p><div className="llm-refs">{selected.body.llm.output.evidence_references.map(id => <code key={id}>{id}</code>)}</div>{(selected.body.llm.output.knowledge_references?.length ?? 0) > 0 && <div className="llm-refs knowledge-refs">{selected.body.llm.output.knowledge_references!.map(id => <code key={id}>{id}</code>)}</div>}<ul className="llm-cause-list">{selected.body.llm.output.root_causes.map(cause => <li key={cause.cause}><strong>{cause.cause}</strong><span>{cause.reasoning}</span></li>)}</ul></> : <p className="muted">本次建议由确定性规则生成，LLM 未参与（{llmReasonLabels[selected.body.llm.reason ?? ''] ?? selected.body.llm.reason ?? '未调用'}）。</p>}</div>}<div className="impact-box"><div><span>条件性利润改善情景</span><strong>{money(selected.body.expected_impact.profit_improvement_min)} — {money(selected.body.expected_impact.profit_improvement_max)}</strong><small>未来 {selected.body.expected_impact.horizon_days} 天 · 非实际收益或保证值</small></div><p>{selected.body.expected_impact.explanation}</p></div><div className="detail-section"><h3>待执行的模拟动作 <span className="tiny-tag">SIMULATION ONLY</span></h3>{selected.action.type === 'adjust_ad_budget' ? <div className="budget-preview"><div><span>参考日预算</span><strong>{money(selected.action.baseline_daily_budget)}</strong></div><ArrowRight size={18} /><div><span>模拟新预算</span><strong>{money(selected.action.proposed_daily_budget)}</strong></div></div> : <p>为 {selected.action.sku} 创建成本核查任务预览。</p>}<p className="muted">目标 SKU：{selected.action.sku}。该动作只写入模拟记录，不会调用广告、采购或支付系统。</p></div><div className="detail-columns"><div><h3><TriangleAlert size={16} />风险与假设</h3><ul>{[...selected.body.risks, ...selected.body.assumptions].map(value => <li key={value}>{value}</li>)}</ul></div><div><h3><Database size={16} />待补充信息</h3><ul>{selected.body.missing_information.map(value => <li key={value}>{value}</li>)}</ul></div></div><div className="approval-actions"><div className="approval-title"><ShieldCheck size={18} /><strong>人工审批与模拟执行</strong><span className="tiny-tag">SIMULATION ONLY</span></div>{!isApprover && <p className="muted">当前为经营分析师。请在右上角切换“审批负责人”进行审批与模拟执行。</p>}{selected.status === 'pending_approval' && isApprover && <><label className="reason-label">审批意见<textarea aria-label="审批意见" value={reason} onChange={event => setReason(event.target.value)} rows={2} /></label><div className="action-row"><button className="button danger" onClick={() => decide('reject')} disabled={!!busy || reason.trim().length < 2}><X size={15} />拒绝建议</button><button className="button primary" onClick={() => decide('approve')} disabled={!!busy || reason.trim().length < 2}><Check size={15} />批准建议</button></div></>}{selected.approval_reason && <p className="decision-note">审批意见：{selected.approval_reason}</p>}{isApprover && ['approved', 'executed', 'execution_failed'].includes(selected.status) && <button className="button primary" onClick={execute} disabled={!!busy}>{busy === 'execute' ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{selected.status === 'approved' ? '模拟执行已批准动作' : '再次请求 · 验证幂等'}</button>}<div className="state-flow">{['pending_approval', 'approved', 'executing', 'executed'].map((value, index) => <span key={value} className={selected.status === value ? 'current' : ''}>{index > 0 && <ArrowRight size={12} />}{statusLabels[value]}</span>)}</div></div>{(selected.execution || execution?.recommendation_id === selected.id) && <ExecutionResult result={execution?.recommendation_id === selected.id ? execution : selected.execution!} />}</section> : <section className="panel"><Empty title="选择一条建议" text="建议会展示统计范围、证据、影响情景与审批状态。" /></section>}</div>}

        {page === 'knowledge' && <div className="knowledge-layout">
          <section className="panel knowledge-search-panel">
            <div className="panel-heading"><div><h2><BookOpen size={18} />运营知识库</h2><p>{demo?.knowledge ? `${demo.knowledge.documents} 篇文档 · ${demo.knowledge.embedding_model} · ${demo.knowledge.embedding_provider}` : '离线哈希向量 + 词法混合检索'}</p></div><span className="tiny-tag">RAG</span></div>
            <form className="knowledge-search" onSubmit={searchKnowledge}>
              <label>检索问题<input aria-label="知识库检索" value={knowledgeQuery} onChange={event => setKnowledgeQuery(event.target.value)} placeholder="例如：广告预算下调需要谁审批？" /></label>
              <button className="button primary" type="submit" disabled={!!busy || knowledgeQuery.trim().length < 2}>{busy === 'knowledge-search' ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}检索知识</button>
            </form>
            {knowledgeResults ? <KnowledgeHits knowledge={knowledgeResults} /> : <Empty title="从运营规则里找答案" text="检索结果会返回可引用的 chunk_id；低于阈值的查询明确显示“无可靠答案”。" />}
          </section>
          <section className="panel knowledge-documents">
            <div className="panel-heading"><div><h2>知识文档</h2><p>审批负责人可新增或停用；停用不清理审计历史</p></div><span className="count-circle">{documents.length}</span></div>
            {!documents.length ? <Empty title="知识库为空" text="以审批负责人身份添加第一篇 SOP。" /> : <div className="knowledge-doc-list">{documents.map(doc => <article key={doc.id}><div><strong>{doc.title}</strong><small>{doc.source} · {doc.version} · {doc.chunk_count} 个片段</small></div>{isApprover && <button className="text-button danger-text" onClick={() => removeKnowledgeDocument(doc.id)} disabled={!!busy}>停用</button>}</article>)}</div>}
            {isApprover && <form className="knowledge-form" onSubmit={addKnowledgeDocument}>
              <h3>添加知识文档 <span className="tiny-tag">APPROVER ONLY</span></h3>
              <div className="knowledge-form-row">
                <label>标题<input aria-label="知识标题" value={knowledgeDraft.title} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, title: event.target.value })} placeholder="例如：广告预算调整规则" /></label>
                <label>版本<input aria-label="知识版本" value={knowledgeDraft.version} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, version: event.target.value })} /></label>
              </div>
              <label>来源<input aria-label="知识来源" value={knowledgeDraft.source} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, source: event.target.value })} /></label>
              <label>正文<textarea aria-label="知识正文" rows={5} value={knowledgeDraft.content} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, content: event.target.value })} placeholder="# 适用范围&#10;...（支持 Markdown 标题分段）" /></label>
              <button className="button primary" type="submit" disabled={!!busy || knowledgeDraft.title.trim().length < 2 || knowledgeDraft.content.trim().length < 10}>添加知识文档</button>
            </form>}
          </section>
        </div>}

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
        {(output.knowledge_references?.length ?? 0) > 0 && <div className="llm-section"><h3>知识引用</h3><div className="llm-refs knowledge-refs">{output.knowledge_references!.map(id => <code key={id}>{id}</code>)}</div></div>}
      </> : <div className="llm-fallback"><p><strong>{status ? llmStatusLabels[status] ?? status : 'LLM 未参与'}</strong>{llm.reason ? `：${llmReasonLabels[llm.reason] ?? llm.reason}` : ''}</p>{llm.error && <p className="muted">错误码 {llm.error.code} · {llm.error.message}</p>}<p className="muted">{status === 'not_requested' ? '本次分析未调用 LLM；展示的是确定性计算结果。' : '本次结论、证据与建议由确定性规则引擎生成，不是 LLM 输出。'}</p></div>}
    </>}
  </section>
}

function bounded(value: string, limit: number): string {
  return value.length > limit ? `${value.slice(0, Math.max(0, limit - 1))}…` : value
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const SAFE_STORE_ID = /^[A-Za-z0-9._-]{1,64}$/

function safeArgumentText(value: unknown, pattern: RegExp, maxLength: number): string | null {
  return typeof value === 'string' && value.length <= maxLength && pattern.test(value) ? value : null
}

function toolArgumentSummary(arguments_: Record<string, unknown> | null | undefined): string {
  if (!arguments_ || typeof arguments_ !== 'object' || Array.isArray(arguments_)) return '参数不可用'
  const parts: string[] = []
  const period = arguments_.period
  if (period && typeof period === 'object' && !Array.isArray(period)) {
    const fields = period as Record<string, unknown>
    const start = safeArgumentText(fields.start_date, ISO_DATE, 10)
    const end = safeArgumentText(fields.end_date, ISO_DATE, 10)
    if (start && end) parts.push(`${start} → ${end}`)
    else parts.push('period 已隐藏')
  }
  if (arguments_.store_id === null) parts.push('全部店铺')
  else if (arguments_.store_id !== undefined) {
    const store = safeArgumentText(arguments_.store_id, SAFE_STORE_ID, 64)
    parts.push(store ? `店铺 ${store}` : 'store_id 已隐藏')
  }
  const extra = Object.keys(arguments_).filter(key => key !== 'period' && key !== 'store_id')
  if (extra.length) parts.push(`${extra.length} 个其他字段`)
  if (!parts.length) parts.push('无安全参数摘要')
  return bounded(parts.join(' · '), 140)
}

function AgentTrace({ llm }: { llm?: LLMRun | null }) {
  if (!llm) return null
  const rounds = llm.rounds ?? []
  const calls = llm.tool_calls ?? []
  const native = llm.native_tools === true
  return <section className="panel trace-panel" aria-label="Agent 执行轨迹">
    <div className="panel-heading"><div><h2><Activity size={17} />Agent 执行轨迹</h2><p>{native ? '原生 Function Calling · 只读工具循环' : '本次未使用原生工具循环'}</p></div><span className={`llm-badge trace-status ${llm.status}`}>{llmStatusLabels[llm.status] ?? llm.status}</span></div>
    {!native ? <p className="trace-empty">{llm.status === 'not_requested' ? '本次分析未触发 LLM，因此没有模型轮次或工具调用。' : '本次分析走单次 JSON 路径，未产生原生工具轮次。'}</p> : <>
      <div className="trace-meta">
        <span>模型<strong>{llm.model}</strong></span>
        <span>轮次<strong>{rounds.length}</strong></span>
        <span>工具调用<strong>{calls.length}</strong></span>
        <span>总 Token<strong>{llm.usage?.total_tokens ?? '—'}</strong></span>
        <span>总耗时<strong>{llm.latency_ms == null ? '—' : `${llm.latency_ms.toFixed(1)} ms`}</strong></span>
        <span>终止原因<strong>{terminationLabels[llm.termination_reason ?? ''] ?? llm.termination_reason ?? '—'}</strong></span>
      </div>
      {!rounds.length ? <p className="trace-empty">没有可展示的模型轮次。</p> : <ol className="trace-rounds">
        {rounds.map(round => {
          const roundCalls = calls.filter(call => call.round === round.round)
          return <li className="trace-round" key={round.round}>
            <div className="trace-round-head">
              <span className="trace-round-no">ROUND {round.round}</span>
              <span className="trace-finish">{round.finish_reason ?? '—'}</span>
              <span>{round.tool_call_count} 次工具</span>
              <span>{round.latency_ms.toFixed(1)} ms</span>
              <span>{round.usage?.total_tokens == null ? 'Token —' : `${round.usage.total_tokens} tokens`}</span>
            </div>
            {roundCalls.length ? <div className="trace-calls">{roundCalls.map(call => <div className={`trace-call ${call.ok ? 'ok' : 'failed'}`} key={call.tool_call_id}>
              <div className="trace-call-head"><code>{call.name}</code><span className={`trace-outcome ${call.ok ? 'ok' : 'failed'}`}>{call.ok ? '✓ 成功' : '× 失败'}</span><span>{call.latency_ms.toFixed(1)} ms</span></div>
              <p className="trace-args"><span>参数</span>{toolArgumentSummary(call.arguments)}</p>
              <p className="trace-result"><span>结果</span>{bounded(call.result_summary, 160)}</p>
              {!call.ok && <p className="trace-error">错误码：{call.error_code ?? '未记录'}</p>}
            </div>)}</div> : <p className="trace-no-call">本轮无工具调用</p>}
          </li>
        })}
      </ol>}
      {llm.error && <p className="trace-error">本次运行错误：{llm.error.code} · {bounded(llm.error.message, 180)}</p>}
    </>}
  </section>
}

function KnowledgeHits({ knowledge, showCited = false }: { knowledge: KnowledgeSearch | KnowledgeContext; showCited?: boolean }) {
  if (knowledge.no_answer) return <div className="knowledge-no-answer"><TriangleAlert size={18} /><div><strong>无可靠答案</strong><p>没有片段达到相似度阈值；系统没有把低相关结果交给模型引用。</p></div></div>
  return <div className="knowledge-hits">{knowledge.hits.map(hit => {
    const cited = 'cited' in hit && hit.cited
    return <article className="knowledge-hit" key={hit.chunk_id}>
      <div className="knowledge-hit-head"><strong>{hit.title}</strong>{showCited && <span className={`citation-badge ${cited ? 'cited' : ''}`}>{cited ? '✓ LLM 已引用' : '仅检索参考'}</span>}<span className="knowledge-score">{hit.score.toFixed(3)}</span></div>
      <div className="knowledge-hit-meta"><span>{hit.source}</span><span>{hit.version}</span>{hit.heading && <span>{hit.heading}</span>}</div>
      <p>{bounded(hit.content, 360)}</p>
      <code>{hit.chunk_id}</code>
    </article>
  })}</div>
}

function KnowledgePanel({ knowledge, showCited = false }: { knowledge?: KnowledgeSearch | KnowledgeContext | null; showCited?: boolean }) {
  if (!knowledge) return null
  return <section className="panel knowledge-panel">
    <div className="panel-heading"><div><h2><BookOpen size={17} />知识库参考</h2><p>{knowledge.no_answer ? '未找到可靠匹配' : `${knowledge.hits.length} 个片段 · ${knowledge.embedding_model} · ${knowledge.retrieval_ms.toFixed(1)} ms`}</p></div><span className={`llm-badge ${knowledge.no_answer ? 'deterministic_fallback' : 'llm_success'}`}>{knowledge.no_answer ? '无可靠答案' : '已检索'}</span></div>
    <KnowledgeHits knowledge={knowledge} showCited={showCited} />
    <p className="muted knowledge-note">知识片段作为不可信数据传给模型；只有本次检索结果中的 chunk_id 才能通过引用校验。</p>
  </section>
}

function ExecutionResult({ result }: { result: Execution }) {
  return <div className={`execution-result ${result.status === 'execution_failed' ? 'failure' : ''}`}><div><CheckCircle2 size={20} /><h3>{result.status === 'execution_failed' ? '模拟执行失败' : '模拟执行结果'}<span className="tiny-tag">SIMULATION</span></h3></div><p>{result.error?.message ?? result.result?.message}</p>{result.result?.before && <div className="budget-preview"><div><span>参考日预算</span><strong>{money(result.result.before.daily_budget)}</strong></div><ArrowRight size={18} /><div><span>模拟新预算</span><strong>{money(result.result.after!.daily_budget)}</strong></div></div>}<p className="mono">执行 ID：{result.execution_id}</p><div className="result-foot"><span><Check size={13} />外部调用 {result.result?.external_calls ?? 0} 次</span><span><Fingerprint size={13} />{result.replayed ? '命中幂等，复用原执行记录' : '唯一执行记录已保存'}</span></div></div>
}

import { lazy, Suspense, useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, BookOpen, Check, CheckCircle2, CircleDollarSign, Database, Fingerprint, LoaderCircle, Megaphone, Play, RefreshCw, Search, ShieldCheck, Sparkles, Terminal, TriangleAlert, X, type LucideIcon } from 'lucide-react'
import { api, ApiError } from './api'
import type { Analysis, Audit, Demo, Execution, Identity, JobStatus, KnowledgeContext, KnowledgeDocument, KnowledgeSearch, LLMConfig, LLMRun, Metrics, OpsSummary, Recommendation, Scope } from './types'
import { dateTime, llmReasonLabels, llmStatusLabels, money, percent, signedMoney, statusLabels, terminationLabels } from './format'
import { pages, type Page } from './navigation'
import { cn } from './lib/utils'
import { AppSidebar } from './components/layout/app-sidebar'
import { AppHeader } from './components/layout/app-header'
import { IntelligencePage } from './components/intelligence-page'
import { ImportPage } from './components/imports/import-page'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader } from './components/ui/card'
import { Input } from './components/ui/input'
import { NativeSelect } from './components/ui/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'
import { Textarea } from './components/ui/textarea'
import './styles.css'

const TrendChart = lazy(() => import('./components/TrendChart').then(module => ({ default: module.TrendChart })))

const jobStatusOrder: JobStatus[] = [
  'queued', 'running', 'waiting_approval', 'completed', 'insufficient_data', 'failed',
  'approved', 'rejected', 'executed', 'execution_failed',
]
const busyStatuses = ['queued', 'running']
const usd = (value: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(value)
const countText = (value: number | null) => value == null ? '暂无数据' : value.toLocaleString('zh-CN')

function Status({ value }: { value: string }) {
  const positive = ['executed', 'completed', 'approved'].includes(value)
  const negative = ['failed', 'execution_failed', 'rejected'].includes(value)
  const pending = ['waiting_approval', 'pending_approval', 'insufficient_data'].includes(value)
  return <Badge variant={positive ? 'success' : negative ? 'danger' : pending ? 'warning' : 'secondary'} className='gap-1.5 font-medium'>
    <span className={cn('size-1.5 rounded-full', positive ? 'bg-emerald-600 dark:bg-emerald-400' : negative ? 'bg-destructive' : pending ? 'bg-amber-600 dark:bg-amber-400' : 'bg-muted-foreground/60')} />
    {statusLabels[value] ?? value}
  </Badge>
}

function Empty({ title, text, children }: { title: string; text: string; children?: ReactNode }) {
  return <div className='flex min-h-[200px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center'>
    <Database className='size-6 text-muted-foreground/60' />
    <h3 className='text-sm font-medium'>{title}</h3>
    <p className='max-w-sm text-sm leading-relaxed text-muted-foreground'>{text}</p>
    {children && <div className='mt-2'>{children}</div>}
  </div>
}

function SectionHeading({ title, description, icon, action }: { title: ReactNode; description?: ReactNode; icon?: ReactNode; action?: ReactNode }) {
  return <div className='flex items-start justify-between gap-3 border-b px-5 py-4'>
    <div className='min-w-0 space-y-1'>
      <h2 className='flex items-center gap-2 text-base font-semibold leading-none tracking-tight'>{icon}{title}</h2>
      {description && <p className='text-xs text-muted-foreground'>{description}</p>}
    </div>
    {action}
  </div>
}

function KpiCard({ label, value, sub, icon: Icon, delta, favorable }: { label: string; value: string; sub: string; icon: LucideIcon; delta: ReactNode; favorable: boolean }) {
  return <Card className='gap-0 py-0'>
    <CardHeader className='flex flex-row items-center justify-between px-5 pt-5 pb-2'>
      <CardDescription className='text-sm'>{label}</CardDescription>
      <Icon className='size-4 text-muted-foreground' />
    </CardHeader>
    <CardContent className='px-5 pb-3'>
      <div className='text-2xl font-semibold tracking-tight tabular-nums'>{value}</div>
      <div className='mt-1.5 flex items-center gap-2'>
        <Badge variant={favorable ? 'success' : 'danger'} className='gap-0.5 px-1.5 text-[11px] font-medium'>{delta}</Badge>
        <span className='text-xs text-muted-foreground'>较上一周期</span>
      </div>
    </CardContent>
    <CardFooter className='border-t px-5 py-2.5 text-xs text-muted-foreground'>{sub}</CardFooter>
  </Card>
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
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = typeof localStorage === 'undefined' ? null : localStorage.getItem('profitops-theme')
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('profitops-sidebar-collapsed') === 'true')
  const [mobileOpen, setMobileOpen] = useState(false)
  const loadSequence = useRef(0)
  const opsSequence = useRef(0)
  // A freshly created recommendation may not be in the last list response yet.
  // Never fall back to another item: approval and execution must target the
  // recommendation the user opened, not whatever happens to be first.
  const selected = selectedId ? recommendations.find(item => item.id === selectedId) : recommendations[0]
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
    // A slower response for a previous window must never replace the window
    // the operator has already switched to.
    const sequence = ++opsSequence.current
    setOpsLoading(true); setError('')
    try {
      const next = await api.ops(identity.token, opsHours)
      if (sequence === opsSequence.current) setOps(next)
    } catch (value) {
      if (sequence === opsSequence.current) showError(value)
    } finally {
      if (sequence === opsSequence.current) setOpsLoading(false)
    }
  }, [identity, opsHours, showError])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (page === 'ops' && isApprover) void loadOps()
  }, [page, isApprover, loadOps])
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem('profitops-theme', theme)
  }, [theme])
  useEffect(() => {
    localStorage.setItem('profitops-sidebar-collapsed', String(collapsed))
  }, [collapsed])

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
  function searchKnowledge(event: FormEvent) {
    event.preventDefault()
    if (!identity || knowledgeQuery.trim().length < 2) return
    void action('knowledge-search', async () => {
      const result = await api.knowledgeSearch(identity.token, knowledgeQuery.trim())
      setKnowledgeResults(result)
      setNotice(result.no_answer ? '知识库没有达到阈值的可靠片段。' : `检索到 ${result.hits.length} 个知识片段。`)
    })
  }
  function addKnowledgeDocument(event: FormEvent) {
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
  function changeIdentity(next: Identity) {
    setIdentity(next)
    setNotice(`已切换为${next.name}；权限由后端 token 校验`)
    setError('')
  }

  const activePage = pages.find(item => item.id === page)!
  const renderButton = (label = '启动 Profit Agent') => <Button onClick={startAnalysis} disabled={!!busy || !metrics || busyStatuses.includes(analysis?.status ?? '')}>{busy === 'analysis' || busyStatuses.includes(analysis?.status ?? '') ? <LoaderCircle className='size-4 animate-spin' /> : <Sparkles className='size-4' />}{label}</Button>

  return <div className='flex min-h-svh bg-background'>
    <AppSidebar
      page={page}
      pending={pending}
      collapsed={collapsed}
      mobileOpen={mobileOpen}
      demo={demo}
      identity={identity}
      isApprover={isApprover}
      onNavigate={next => { setPage(next); setError(''); setMobileOpen(false) }}
      onCloseMobile={() => setMobileOpen(false)}
    />

    <div className={cn('flex min-h-svh w-full flex-col transition-none duration-200 md:transition-[padding]', collapsed ? 'md:pl-14' : 'md:pl-64')}>
      <AppHeader
        pageLabel={activePage.label}
        collapsed={collapsed}
        demo={demo}
        identity={identity}
        theme={theme}
        onToggleCollapsed={() => setCollapsed(value => !value)}
        onOpenMobile={() => setMobileOpen(true)}
        onIdentityChange={changeIdentity}
        onToggleTheme={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}
      />

      <main className='flex-1'>
        <div className='mx-auto w-full max-w-[1600px] space-y-6 px-4 py-6 md:px-6 lg:py-8'>
          <div className='flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between'>
            <div className='space-y-1'>
              <h1 className='text-2xl font-bold tracking-tight'>{activePage.label}</h1>
              <p className='text-sm text-muted-foreground'>{activePage.detail}</p>
            </div>
            <div className='flex flex-wrap items-center gap-2'>
              <Button variant='outline' size='sm' onClick={() => { setError(''); if (page === 'ops') void loadOps(); else void refresh() }} disabled={loading || opsLoading || !!busy}>
                <RefreshCw className={cn('size-4', (loading || opsLoading) && 'animate-spin')} />刷新数据
              </Button>
              {!['audit', 'knowledge', 'ops', 'imports'].includes(page) && renderButton()}
            </div>
          </div>

          {error && <div className='flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive' role='alert'>
            <TriangleAlert className='mt-0.5 size-4 shrink-0' />
            <span className='flex-1 break-words'>{error}</span>
            <button type='button' className='rounded-sm opacity-70 transition-opacity hover:opacity-100' aria-label='关闭错误' onClick={() => setError('')}><X className='size-4' /></button>
          </div>}
          {notice && <div className='flex items-start gap-3 rounded-lg border border-emerald-600/20 bg-emerald-600/5 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-400' role='status'>
            <CheckCircle2 className='mt-0.5 size-4 shrink-0' />
            <span className='flex-1 break-words'>{notice}</span>
            <button type='button' className='rounded-sm opacity-70 transition-opacity hover:opacity-100' aria-label='关闭通知' onClick={() => setNotice('')}><X className='size-4' /></button>
          </div>}

          {(page === 'overview' || page === 'agent') && <div className='flex flex-wrap items-center justify-between gap-3'>
            <div className='flex flex-wrap items-center gap-2'>
              <label className='flex items-center gap-2 text-sm text-muted-foreground'>统计周期
                <NativeSelect aria-label='统计周期' className='w-[170px]' value={scope.days} onChange={event => setScope({ ...scope, days: Number(event.target.value) })}>
                  <option value={30}>最近 30 个数据日</option>
                  <option value={7}>最近 7 个数据日</option>
                </NativeSelect>
              </label>
              <label className='flex items-center gap-2 text-sm text-muted-foreground'>店铺
                <NativeSelect aria-label='店铺' className='w-[150px]' value={scope.storeId} onChange={event => setScope({ ...scope, storeId: event.target.value })}>
                  <option value=''>全部店铺</option>
                  {demo?.stores.map(store => <option key={store.id} value={store.id}>{store.name}</option>)}
                </NativeSelect>
              </label>
            </div>
            <span className='text-xs text-muted-foreground'>{metrics ? `${metrics.period.start_date} — ${metrics.period.end_date} · UTC · USD` : '等待数据连接'}</span>
          </div>}

          {loading && !metrics && <div className='flex min-h-[200px] items-center justify-center gap-2 text-sm text-muted-foreground' role='status'><LoaderCircle className='size-5 animate-spin' />正在读取经营账本…</div>}
          {!loading && !demo && <Empty title='后端连接失败' text='启动后端后刷新页面，API 默认通过本机 8010 端口提供服务。'><Button variant='outline' onClick={() => window.location.reload()}>重新连接</Button></Empty>}
          {demo && !demo.data_range && !loading && <Empty title='从第一份经营数据开始' text='初始化 60 天演示数据，体验一次完整的利润决策闭环。'><Button onClick={seed} disabled={!!busy}><Database className='size-4' />初始化演示数据</Button></Empty>}

          {page === 'overview' && metrics && <div className={cn('space-y-6', loading && 'pointer-events-none opacity-60')}>
            <div className='grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4'>{[
              { label: '销售额', value: money(metrics.current.sales), metric: 'sales', icon: CircleDollarSign, favorable: metrics.changes.sales.absolute >= 0, sub: `${metrics.current.orders.toLocaleString()} 笔订单` },
              { label: '净利润', value: money(metrics.current.net_profit), metric: 'net_profit', icon: Activity, favorable: metrics.changes.net_profit.absolute >= 0, sub: `净销售额 ${money(metrics.current.net_sales)}` },
              { label: '净利润率', value: metrics.current.margin_pct === null ? '—' : `${metrics.current.margin_pct.toFixed(2)}%`, metric: 'margin_pct', icon: BarChart3, favorable: metrics.changes.margin_pct.absolute >= 0, sub: '净利润 / 净销售额' },
              { label: '广告支出', value: money(metrics.current.ad_spend), metric: 'ad_spend', icon: Megaphone, favorable: metrics.changes.ad_spend.absolute <= 0, sub: '按每日广告账单入账' },
            ].map(item => <KpiCard
              key={item.metric}
              label={item.label}
              value={item.value}
              sub={item.sub}
              icon={item.icon}
              favorable={item.favorable}
              delta={<>
                {metrics.changes[item.metric].absolute >= 0 ? <ArrowUpRight className='size-3' /> : <ArrowDownRight className='size-3' />}
                {item.metric === 'margin_pct' ? `${metrics.changes.margin_pct.absolute > 0 ? '+' : ''}${metrics.changes.margin_pct.absolute.toFixed(2)} pp` : percent(metrics.changes[item.metric].pct)}
              </>}
            />)}</div>

            {!metrics.data_quality.complete && <div className='flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive'>
              <TriangleAlert className='mt-0.5 size-4 shrink-0' />
              <div><strong className='font-medium'>数据覆盖不足</strong><p className='mt-0.5 text-xs'>{metrics.data_quality.issues.join('；')}</p></div>
            </div>}

            <Card className={cn('flex-row items-center gap-4 p-5', metrics.anomaly ? 'border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-950/20' : 'border-emerald-200 bg-emerald-50/50 dark:border-emerald-900/50 dark:bg-emerald-950/20')}>
              <div className={cn('flex size-10 shrink-0 items-center justify-center rounded-full', metrics.anomaly ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400')}>
                <TriangleAlert className='size-5' />
              </div>
              <div className='min-w-0 flex-1 space-y-1'>
                <div className='flex flex-wrap items-center gap-2 text-xs'>
                  <span className={cn('font-semibold tracking-wide', metrics.anomaly ? 'text-amber-700 dark:text-amber-400' : 'text-emerald-700 dark:text-emerald-400')}>PROFIT SIGNAL</span>
                  <span className='text-muted-foreground'>{metrics.anomaly ? '发现经营异常' : '经营信号检查完成'}</span>
                </div>
                <h2 className='text-base font-semibold'>{metrics.anomaly ? '销售在增长，利润却没有跟上。' : '当前周期未发现销售与利润背离。'}</h2>
                <p className='text-sm leading-relaxed text-muted-foreground'>{metrics.anomaly ? `销售额环比 ${percent(metrics.changes.sales.pct)}，净利润环比 ${percent(metrics.changes.net_profit.pct)}。${metrics.drivers[0]?.label}贡献 ${signedMoney(metrics.drivers[0]?.contribution ?? 0)}，值得优先关注。` : '可启动 Agent 核对经营数据、利润桥与完整性。'}</p>
              </div>
              <Button variant='ghost' className='shrink-0' onClick={startAnalysis} disabled={!!busy}>分析原因<ArrowRight className='size-4' /></Button>
            </Card>

            <div className='grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]'>
              <Suspense fallback={<Card className='h-[380px] animate-pulse bg-muted/40' />}><TrendChart metrics={metrics} /></Suspense>
              <Card className='gap-0 py-0'>
                <SectionHeading title='利润变动归因' description='每一项，均可回算到账本' action={<Badge variant='outline'>USD</Badge>} />
                <CardContent className='px-5 py-4'>
                  <div className='flex items-center justify-between border-b pb-4 text-sm'>
                    <span className='text-muted-foreground'>净利润变化</span>
                    <span className='text-xl font-semibold tabular-nums'>{signedMoney(metrics.changes.net_profit.absolute)}</span>
                  </div>
                  <div className='mt-4 space-y-3'>{metrics.drivers.map(driver => <div key={driver.metric}>
                    <div className='flex items-center justify-between gap-3 text-xs'>
                      <span className='text-muted-foreground'>{driver.label}</span>
                      <span className={cn('font-medium tabular-nums', driver.contribution < 0 && 'text-destructive')}>{signedMoney(driver.contribution)}</span>
                    </div>
                    <div className='mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted'>
                      <span className={cn('block h-full rounded-full', driver.contribution < 0 ? 'bg-destructive/70' : 'bg-emerald-600/70')} style={{ width: `${Math.max(1, Math.abs(driver.contribution) / Math.max(...metrics.drivers.map(item => Math.abs(item.contribution)), 1) * 100)}%` }} />
                    </div>
                  </div>)}</div>
                  <div className='mt-4 flex items-center gap-1.5 border-t pt-3 text-xs text-emerald-600 dark:text-emerald-400'>
                    <CheckCircle2 className='size-3.5' />{metrics.bridge_verified ? '贡献项合计与利润变化一致' : '贡献核对异常'}
                  </div>
                </CardContent>
              </Card>
            </div>

            <Card className='gap-0 overflow-hidden py-0'>
              <SectionHeading title='商品经营表现' description='找到需要优先关注的 SKU' action={<Badge variant='outline'>{metrics.sku_breakdown.length} 个商品</Badge>} />
              <Table>
                <TableHeader><TableRow><TableHead>商品 / SKU</TableHead><TableHead>销售额</TableHead><TableHead>净利润</TableHead><TableHead>利润变化</TableHead><TableHead>广告支出</TableHead><TableHead>库存 / 在途</TableHead></TableRow></TableHeader>
                <TableBody>{metrics.sku_breakdown.map(row => <TableRow key={row.sku}>
                  <TableCell><strong className='font-medium'>{row.name}</strong><span className='block text-xs text-muted-foreground'>{row.sku}</span></TableCell>
                  <TableCell className='tabular-nums'>{money(row.current.sales)}</TableCell>
                  <TableCell className='tabular-nums'>{money(row.current.net_profit)}</TableCell>
                  <TableCell className={cn('tabular-nums', row.profit_delta < 0 && 'text-destructive')}>{signedMoney(row.profit_delta)}</TableCell>
                  <TableCell className='tabular-nums'>{money(row.current.ad_spend)}</TableCell>
                  <TableCell className='tabular-nums'>{row.inventory} <span className='text-muted-foreground'>/ {row.inbound_inventory}</span></TableCell>
                </TableRow>)}</TableBody>
              </Table>
            </Card>

            <div className='flex flex-wrap items-start gap-2 text-xs text-muted-foreground'>
              <Database className='mt-0.5 size-3.5 shrink-0' />
              <span className='flex-1'>净利润 = 销售额 − 折扣 − 退款 − 采购 − 广告 − 履约 − 平台费 − 其他可变成本。零分母显示“无可比基数”。</span>
              <button type='button' className='font-medium text-foreground underline-offset-4 hover:underline' onClick={seed} disabled={!!busy}>重新检查演示数据</button>
            </div>
          </div>}

          {page === 'agent' && <div className='grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]'>
            <Card className='gap-0 self-start py-0'>
              <SectionHeading
                title='Profit Agent'
                icon={<Sparkles className='size-4' />}
                description={`LangGraph · ${analysis?.result?.llm?.status === 'llm_success' ? `DeepSeek ${analysis.result?.llm?.model}` : demo?.llm?.configured ? `DeepSeek ${demo.llm.model} · 待调用` : '确定性规则引擎'}`}
                action={analysis ? <Status value={analysis.status} /> : undefined}
              />
              <CardContent className='px-5 py-4'>
                {!analysis ? <Empty title='开始一次利润诊断' text='Agent 会读取数据、计算利润、核对证据，并将建议交给你审批。'>{renderButton('启动分析')}</Empty> : <>
                  <div className='flex flex-wrap gap-x-4 gap-y-1 border-b pb-3 text-xs text-muted-foreground'>
                    <span>RUN {analysis.id.slice(0, 8)}</span>
                    <span>尝试 {analysis.attempts}/{analysis.max_attempts}</span>
                    <span>{dateTime(analysis.created_at)}</span>
                    {analysis.next_run_at && <span>下次重试 {dateTime(analysis.next_run_at)}</span>}
                  </div>
                  <div className='mt-4 space-y-0'>{analysis.steps.map((step, index) => <div className='relative flex gap-3' key={`${step.name}-${index}`}>
                    <div className='flex flex-col items-center'>
                      <div className={cn('z-10 flex size-6 shrink-0 items-center justify-center rounded-full border bg-background', step.status === 'completed' ? 'border-emerald-600/30 bg-emerald-600/10 text-emerald-600 dark:text-emerald-400' : step.status === 'failed' ? 'border-destructive/30 bg-destructive/10 text-destructive' : 'text-muted-foreground')}>
                        {step.status === 'completed' ? <Check className='size-3.5' /> : step.status === 'failed' ? <X className='size-3.5' /> : <LoaderCircle className='size-3.5 animate-spin' />}
                      </div>
                      {index < analysis.steps.length - 1 && <div className='w-px flex-1 bg-border' />}
                    </div>
                    <div className='min-w-0 flex-1 pb-4'>
                      <div className='flex items-center justify-between gap-2 text-sm'>
                        <span className='font-medium'>{step.label}</span>
                        <span className='text-xs text-muted-foreground'>{step.duration_ms == null ? '运行中' : `${step.duration_ms.toFixed(1)} ms`}</span>
                      </div>
                      <p className='mt-0.5 text-xs text-muted-foreground'>{step.summary ?? '正在执行工具…'}</p>
                      <code className='mt-1 block text-[11px] text-muted-foreground/80'>{step.name}</code>
                    </div>
                  </div>)}</div>
                  {busyStatuses.includes(analysis.status) && <div className='mt-2 flex items-center gap-2 text-xs text-muted-foreground'><LoaderCircle className='size-3.5 animate-spin' />正在通过后端工作流处理…</div>}
                  {analysis.error && <div className='mt-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive'>{analysis.error.code}：{analysis.error.message}</div>}
                  {analysis.status === 'failed' && <Button variant='outline' size='sm' className='mt-3' onClick={retryAnalysis} disabled={!!busy}>{busy === 'analysis-retry' ? <LoaderCircle className='size-3.5 animate-spin' /> : <RefreshCw className='size-3.5' />}重新排队执行</Button>}
                  <div className='mt-4 flex items-start gap-1.5 border-t pt-3 text-xs text-muted-foreground'><Fingerprint className='mt-0.5 size-3.5 shrink-0' /><span className='break-all'>请求 ID：{analysis.request_id}</span></div>
                </>}
              </CardContent>
              {analyses.length > 1 && <label className='flex flex-col gap-1.5 border-t px-5 py-3 text-xs text-muted-foreground'>查看历史分析
                <NativeSelect aria-label='历史分析' value={analysis?.id ?? ''} onChange={event => { const value = analyses.find(item => item.id === event.target.value); if (value) setAnalysis(value) }}>
                  {analyses.map(item => <option value={item.id} key={item.id}>{item.id.slice(0, 8)} · {statusLabels[item.status]} · {dateTime(item.created_at)}</option>)}
                </NativeSelect>
              </label>}
            </Card>

            <div className='space-y-6'>
              <Card className='gap-0 py-0'>
                <SectionHeading title='分析结论与依据' description='计算结果绑定统计周期与数据来源' action={<Terminal className='size-4 text-muted-foreground' />} />
                <CardContent className='px-5 py-4'>
                  {analysis?.result ? <>
                    <div className='rounded-lg bg-muted/50 p-4'>
                      <span className='text-[11px] font-medium tracking-wide text-muted-foreground'>AGENT FINDING</span>
                      <h3 className='mt-2 text-sm font-medium leading-relaxed'>{analysis.result.findings?.conclusion}</h3>
                      <p className='mt-2 text-xs leading-relaxed text-muted-foreground'>当前：{analysis.result.metrics.period.start_date} — {analysis.result.metrics.period.end_date}<br />上一周期：{analysis.result.metrics.previous_period.start_date} — {analysis.result.metrics.previous_period.end_date}</p>
                    </div>
                    <div className='mt-4 grid grid-cols-2 gap-4'>
                      <div><span className='text-xs text-muted-foreground'>净利润变化</span><div className='mt-1 text-xl font-semibold tabular-nums'>{signedMoney(analysis.result.metrics.changes.net_profit.absolute)}</div></div>
                      <div><span className='text-xs text-muted-foreground'>数据覆盖</span><div className='mt-1 text-xl font-semibold tabular-nums'>{analysis.result.metrics.data_quality.observed_records} / {analysis.result.metrics.data_quality.expected_records}</div></div>
                    </div>
                    <div className='mt-4'>{analysis.result.findings?.evidence.slice(0, 4).map(item => <div key={item.id} className='flex items-center justify-between gap-3 border-t py-2.5 text-sm first:border-t-0'>
                      <span>{item.label}<small className='block text-xs text-muted-foreground'>{item.source}</small></span>
                      <strong className='font-medium tabular-nums'>{signedMoney(item.contribution)}</strong>
                    </div>)}</div>
                    {analysis.recommendation_id && <Button className='mt-4 w-full' onClick={() => { setSelectedId(analysis.recommendation_id!); setPage('approvals') }}>查看经营建议<ArrowRight className='size-4' /></Button>}
                  </> : <Empty title='等待分析结果' text='完成计算与证据核验后，将在这里展示结论。' />}
                </CardContent>
              </Card>
              <LLMPanel llm={analysis?.result?.llm} config={demo?.llm} />
              <KnowledgePanel knowledge={analysis?.result?.knowledge} />
              <AgentTrace llm={analysis?.result?.llm} />
              <Card className='flex-row gap-3 bg-muted/40 p-5 shadow-none'>
                <ShieldCheck className='mt-0.5 size-5 shrink-0 text-emerald-600 dark:text-emerald-400' />
                <div>
                  <h3 className='text-sm font-medium'>让数据决定结论，让人决定行动</h3>
                  <p className='mt-1.5 text-sm leading-relaxed text-muted-foreground'>不完整的数据会暂停建议生成。建议必须经人工审批；模拟只产生数据库预览记录，真实广告和资金均不变。</p>
                  <span className='mt-2 block text-xs text-muted-foreground/80'>当前引擎：规则与本地工具 · 无模型调用</span>
                </div>
              </Card>
            </div>
          </div>}

          {page === 'ops' && <div className='space-y-6'>
            {!isApprover ? <Card className='p-5'><Empty title='需要审批负责人权限' text='Agent 运维视图包含跨任务的失败详情与模型成本，仅审批负责人可查看。请使用右上角切换为审批负责人。' /></Card> : <>
              <div className='flex flex-wrap items-center justify-between gap-3'>
                <label className='flex items-center gap-2 text-sm text-muted-foreground'>统计窗口
                  <NativeSelect aria-label='运维统计窗口' className='w-[150px]' value={opsHours} onChange={event => setOpsHours(Number(event.target.value))}>
                    <option value={24}>最近 24 小时</option>
                    <option value={72}>最近 3 天</option>
                    <option value={168}>最近 7 天</option>
                  </NativeSelect>
                </label>
                <span className='text-xs text-muted-foreground'>{ops ? `${ops.window.start} — ${ops.window.end} · UTC · 持久化记录` : '等待持久化记录'}</span>
              </div>
              {opsLoading && !ops && <Card><div className='flex min-h-[200px] items-center justify-center gap-2 text-sm text-muted-foreground' role='status'><LoaderCircle className='size-5 animate-spin' />正在聚合任务、模型轨迹与审计记录…</div></Card>}
              {!opsLoading && !ops && <Card className='p-5'><Empty title='暂无运维数据' text='当前窗口没有可聚合的任务记录。启动一次分析后，任务、工具调用与成本会显示在这里。' /></Card>}
              {ops && <>
                <div className='grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4'>
                  <Card className='gap-0 px-5 py-4'><span className='text-sm text-muted-foreground'>任务总数</span><strong className='mt-1 text-2xl font-semibold tracking-tight tabular-nums'>{ops.jobs.total}</strong><p className='mt-1 text-xs leading-relaxed text-muted-foreground'>排队 {ops.jobs.by_status.queued ?? 0} · 执行中 {ops.jobs.by_status.running ?? 0} · 待审批 {ops.jobs.by_status.waiting_approval ?? 0}</p></Card>
                  <Card className='gap-0 px-5 py-4'><span className='text-sm text-muted-foreground'>失败任务</span><strong className={cn('mt-1 text-2xl font-semibold tracking-tight tabular-nums', ops.jobs.by_status.failed && 'text-destructive')}>{ops.jobs.by_status.failed ?? 0}</strong><p className='mt-1 text-xs leading-relaxed text-muted-foreground'>死信 {ops.jobs.dead_letter} · 重试 {ops.jobs.retry_count} 次</p></Card>
                  <Card className='gap-0 px-5 py-4'><span className='text-sm text-muted-foreground'>LLM 运行</span><strong className='mt-1 text-2xl font-semibold tracking-tight tabular-nums'>{ops.llm.runs}</strong><p className='mt-1 text-xs leading-relaxed text-muted-foreground'>模型调用 {ops.llm.model_calls} · Provider 请求 {ops.llm.provider_requests}{ops.llm.provider_retries ? `（内部重试 ${ops.llm.provider_retries}）` : ''} · 无模型调用 {ops.llm.no_model_call_runs} · 成功 {ops.llm.by_status.llm_success ?? 0} · 降级 {ops.llm.by_status.deterministic_fallback ?? 0} · 失败 {ops.llm.by_status.llm_failed ?? 0}</p></Card>
                  <Card className='gap-0 px-5 py-4'><span className='text-sm text-muted-foreground'>估算成本</span><strong className='mt-1 text-2xl font-semibold tracking-tight tabular-nums'>{ops.cost.status === 'complete' ? usd(ops.cost.amount_usd ?? 0) : ops.cost.status === 'partial' ? '部分未知' : '暂无数据'}</strong><p className='mt-1 text-xs leading-relaxed text-muted-foreground'>{ops.cost.status === 'complete' ? `${ops.cost.priced_calls} 次调用 · ${ops.cost.pricing_version}` : ops.cost.status === 'partial' ? `${ops.cost.unpriced_calls} 次调用缺少 Usage 或价格${ops.cost.known_subtotal_usd != null ? ` · 已知小计 ${usd(ops.cost.known_subtotal_usd)}` : ''}` : '没有可定价的模型调用'}</p></Card>
                </div>

                <div className='grid grid-cols-1 gap-6 lg:grid-cols-2'>
                  <Card className='gap-0 py-0'>
                    <SectionHeading title='任务队列' description='按窗口内创建的任务聚合' action={<Badge variant='outline'>{ops.window.hours}H</Badge>} />
                    <CardContent className='px-5 py-2'>{jobStatusOrder.map(status => <div className='flex items-center justify-between gap-3 border-t py-2 text-sm first:border-t-0' key={status}><span className='text-muted-foreground'>{statusLabels[status] ?? status}</span><strong className='font-medium tabular-nums'>{ops.jobs.by_status[status] ?? 0}</strong></div>)}
                      <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>死信任务</span><strong className='font-medium tabular-nums text-destructive'>{ops.jobs.dead_letter}</strong></div>
                      <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>重试次数（按 attempts）</span><strong className='font-medium tabular-nums'>{ops.jobs.retry_count}</strong></div>
                      <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>重试调度事件</span><strong className='font-medium tabular-nums'>{ops.jobs.retry_scheduled_events}</strong></div>
                    </CardContent>
                  </Card>

                  <Card className='gap-0 py-0'>
                    <SectionHeading title='模型与工具' description='来自已持久化的 LLM 运行与工具轨迹' action={<Terminal className='size-4 text-muted-foreground' />} />
                    <CardContent className='px-5 py-2'>{Object.entries(ops.llm.by_status).map(([status, count]) => <div className='flex items-center justify-between gap-3 border-t py-2 text-sm first:border-t-0' key={status}><span className='text-muted-foreground'>{llmStatusLabels[status] ?? status}</span><strong className='font-medium tabular-nums'>{count}</strong></div>)}
                      <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>原生工具运行</span><strong className='font-medium tabular-nums'>{ops.llm.native_tool_runs}</strong></div>
                      <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>工具调用 / 失败</span><strong className='font-medium tabular-nums'>{ops.tools.calls} / {ops.tools.failed_calls}</strong></div>
                      <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>工具轮次</span><strong className='font-medium tabular-nums'>{ops.tools.rounds}</strong></div>
                    </CardContent>
                    {ops.tools.by_name.length > 0 && <div className='flex flex-wrap gap-1.5 px-5 pb-4'>{ops.tools.by_name.map(item => <span key={item.name} className='inline-flex items-center gap-1.5 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground'><code className='text-foreground'>{item.name}</code>{item.calls} 次{item.failed ? ` · ${item.failed} 失败` : ''}</span>)}</div>}
                    {ops.llm.termination_reasons.length > 0 && <p className='px-5 pb-4 text-xs leading-relaxed text-muted-foreground'>终止原因：{ops.llm.termination_reasons.map(item => `${terminationLabels[item.reason] ?? item.reason} ×${item.count}`).join('；')}</p>}
                  </Card>

                  <Card className='gap-0 py-0'>
                    <SectionHeading title='Token 与成本' description='缺失用量或价格时显示未知，不按 0 计' action={<CircleDollarSign className='size-4 text-muted-foreground' />} />
                    <CardContent className='px-5 py-4'>
                      <div className='grid grid-cols-2 gap-3 sm:grid-cols-4'>
                        <div><span className='block text-xs text-muted-foreground'>输入 Token</span><strong className='mt-1 block text-base font-medium tabular-nums'>{countText(ops.tokens.prompt)}</strong></div>
                        <div><span className='block text-xs text-muted-foreground'>输出 Token</span><strong className='mt-1 block text-base font-medium tabular-nums'>{countText(ops.tokens.completion)}</strong></div>
                        <div><span className='block text-xs text-muted-foreground'>总 Token</span><strong className='mt-1 block text-base font-medium tabular-nums'>{countText(ops.tokens.total)}</strong></div>
                        <div><span className='block text-xs text-muted-foreground'>缓存命中</span><strong className='mt-1 block text-base font-medium tabular-nums'>{countText(ops.tokens.cached)}</strong></div>
                      </div>
                      <p className='mt-3 text-xs text-muted-foreground'>{ops.tokens.runs_with_usage} 次运行有 Usage{ops.tokens.runs_without_usage ? `，${ops.tokens.runs_without_usage} 次缺失` : ''}。</p>
                      <div className={cn('mt-4 rounded-lg border bg-muted/40 p-4', ops.cost.status === 'partial' && 'border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-950/20')}>
                        <span className='block text-xs text-muted-foreground'>估算成本 · {ops.cost.currency}</span>
                        <strong className='mt-1 block text-2xl font-semibold tracking-tight'>{ops.cost.status === 'complete' ? usd(ops.cost.amount_usd ?? 0) : ops.cost.status === 'partial' ? '未知' : '暂无数据'}</strong>
                        <p className='mt-1 text-xs leading-relaxed text-muted-foreground'>{ops.cost.status === 'complete' ? `${ops.cost.priced_calls} 次调用 · 单价版本 ${ops.cost.pricing_version} · 公开列表价估算，非账单` : ops.cost.status === 'partial' ? `${ops.cost.unpriced_calls} 次调用缺少 Usage 或价格，未按 0 计。${ops.cost.known_subtotal_usd != null ? ` 已知小计 ${usd(ops.cost.known_subtotal_usd)}，仅作诊断，不是总成本。` : ''}` : '没有可定价的模型调用记录。'}</p>
                      </div>
                    </CardContent>
                  </Card>

                  <Card className='gap-0 py-0'>
                    <SectionHeading title='错误与人工环节' description='错误码来自失败任务；审批与执行展示当前状态' action={<TriangleAlert className='size-4 text-muted-foreground' />} />
                    <CardContent className='px-5 py-4'>
                      {!ops.jobs.error_codes.length ? <p className='text-xs text-muted-foreground'>窗口内没有失败错误码。</p> : <div className='flex flex-wrap gap-1.5'>{ops.jobs.error_codes.map(item => <span key={item.code} className='inline-flex items-center gap-1.5 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground'><code className='text-foreground'>{item.code}</code>{item.count} 次</span>)}</div>}
                      <div className='mt-3'>
                        <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>待审批建议</span><strong className='font-medium tabular-nums'>{ops.approvals.by_status.pending_approval ?? 0}</strong></div>
                        <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>已批准建议</span><strong className='font-medium tabular-nums'>{ops.approvals.by_status.approved ?? 0}</strong></div>
                        <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>已执行建议</span><strong className='font-medium tabular-nums'>{ops.approvals.by_status.executed ?? 0}</strong></div>
                        <div className='flex items-center justify-between gap-3 border-t py-2 text-sm'><span className='text-muted-foreground'>模拟执行记录</span><strong className='font-medium tabular-nums'>{ops.executions.total}</strong></div>
                      </div>
                    </CardContent>
                  </Card>
                </div>

                <Card className='ops-failures gap-0 overflow-hidden py-0'>
                  <SectionHeading title='最近失败任务' description='点击查看该分析已保存的执行轨迹' action={<Badge variant='outline'>{ops.recent_failures.length}</Badge>} />
                  {!ops.recent_failures.length ? <div className='p-5'><Empty title='暂无失败任务' text='当前窗口没有失败记录。任务失败或进入死信后会显示在这里。' /></div> : <Table>
                    <TableHeader><TableRow><TableHead>任务 / 请求 ID</TableHead><TableHead>错误</TableHead><TableHead>尝试</TableHead><TableHead>更新时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
                    <TableBody>{ops.recent_failures.map(item => <TableRow key={item.id}>
                      <TableCell><strong className='font-medium'>{item.id.slice(0, 8)}</strong><small className='mono block font-mono text-[11px] text-muted-foreground'>{item.request_id}</small></TableCell>
                      <TableCell className='whitespace-normal'>{item.dead_letter && <Badge variant='danger' className='mb-1'>死信</Badge>}<small className='block text-xs text-muted-foreground'>{item.error_code ?? 'unknown'}：{item.error_message ?? '无错误详情'}</small></TableCell>
                      <TableCell className='tabular-nums'>{item.attempts} / {item.max_attempts}</TableCell>
                      <TableCell>{dateTime(item.updated_at)}</TableCell>
                      <TableCell><button type='button' className='text-sm font-medium text-foreground underline-offset-4 hover:underline' onClick={() => openAnalysis(item.id)} disabled={!!busy}>查看 Trace</button></TableCell>
                    </TableRow>)}</TableBody>
                  </Table>}
                </Card>
                <div className='flex items-start gap-2 text-xs text-muted-foreground'><Database className='mt-0.5 size-3.5 shrink-0' /><span>{ops.source.note} {ops.notes.join(' ')}</span></div>
              </>}
            </>}
          </div>}

          {page === 'weekly' && identity && <IntelligencePage token={identity.token} />}

          {page === 'imports' && identity && <ImportPage
            token={identity.token}
            isApprover={isApprover}
            onImported={() => { void refresh() }}
          />}

          {page === 'approvals' && <div className='grid grid-cols-1 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]'>
            <Card className='gap-0 self-start overflow-hidden py-0'>
              <SectionHeading title='经营建议' description={`${pending} 项等待人工复核`} action={<Badge variant='secondary' className='tabular-nums'>{recommendations.length}</Badge>} />
              {!recommendations.length ? <div className='p-4'><Empty title='还没有待处理建议' text='先启动 Profit Agent，异常分析通过证据核验后会生成建议。' /></div> : recommendations.map(item => <button key={item.id} type='button' className={cn('block w-full border-t px-4 py-4 text-left transition-colors first:border-t-0 hover:bg-muted/50', selected?.id === item.id && 'bg-muted')} onClick={() => { setSelectedId(item.id); setExecution(null); setNotice('') }}>
                <Status value={item.status} />
                <strong className='mt-2 block text-sm font-medium leading-snug'>{item.title}</strong>
                <p className='mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground'>{item.body.recommendation}</p>
                <small className='mt-2 block text-[11px] text-muted-foreground/70'>{dateTime(item.created_at)}</small>
              </button>)}
            </Card>

            {selected ? <Card className='gap-0 py-0'>
              <div className='flex items-start justify-between gap-3 border-b px-5 py-4'>
                <div className='min-w-0 space-y-1'>
                  <span className='text-[11px] font-medium tracking-wide text-muted-foreground'>RECOMMENDATION / {selected.id.slice(0, 8)}</span>
                  <h2 className='text-lg font-semibold tracking-tight'>{selected.title}</h2>
                </div>
                <Status value={selected.status} />
              </div>

              <div className='border-b px-5 py-4'>
                <h3 className='text-sm font-medium'>问题与建议</h3>
                <p className='mt-2 text-sm leading-relaxed text-muted-foreground'>{selected.body.problem}</p>
                <div className='mt-3 flex items-start gap-2 rounded-lg bg-muted/60 p-3 text-sm font-medium'>
                  <Sparkles className='mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400' />
                  <strong className='font-medium'>{selected.body.recommendation}</strong>
                </div>
              </div>

              <div className='border-b px-5 py-4'>
                <div className='flex items-center gap-2'><h3 className='text-sm font-medium'>数据证据</h3><Badge variant='outline' className='text-[10px]'>可核对</Badge></div>
                <p className='mt-1 text-xs text-muted-foreground'>{selected.body.period.start_date} — {selected.body.period.end_date} 对比 {selected.body.previous_period.start_date} — {selected.body.previous_period.end_date} · {selected.body.store_id ?? '全部店铺'} · USD</p>
                <div className='mt-3 overflow-hidden rounded-lg border'>
                  <Table>
                    <TableHeader><TableRow><TableHead>指标 / 数据来源</TableHead><TableHead>上一周期</TableHead><TableHead>当前周期</TableHead><TableHead>利润贡献</TableHead></TableRow></TableHeader>
                    <TableBody>{selected.body.evidence.map(item => <TableRow key={item.id}>
                      <TableCell>{item.label}<small className='block text-xs text-muted-foreground'>{item.source}</small></TableCell>
                      <TableCell className='tabular-nums'>{money(item.previous)}</TableCell>
                      <TableCell className='tabular-nums'>{money(item.current)}</TableCell>
                      <TableCell className='tabular-nums'>{signedMoney(item.contribution)}</TableCell>
                    </TableRow>)}</TableBody>
                  </Table>
                </div>
              </div>

              <div className='border-b px-5 py-4'><KnowledgePanel knowledge={selected.body.knowledge} showCited className='shadow-none' /></div>

              {selected.body.llm && <div className='border-b px-5 py-4'>
                <div className='flex items-center gap-2'><h3 className='text-sm font-medium'>LLM 根因分析</h3><Badge variant='outline' className='text-[10px]'>{llmStatusLabels[selected.body.llm.status] ?? selected.body.llm.status}</Badge></div>
                {selected.body.llm.output ? <>
                  <p className='mt-2 text-sm leading-relaxed text-muted-foreground'>{selected.body.llm.output.summary}</p>
                  <div className='mt-2 flex flex-wrap gap-1'>{selected.body.llm.output.evidence_references.map(id => <code key={id} className='rounded border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground'>{id}</code>)}</div>
                  {(selected.body.llm.output.knowledge_references?.length ?? 0) > 0 && <div className='mt-1.5 flex flex-wrap gap-1'>{selected.body.llm.output.knowledge_references!.map(id => <code key={id} className='rounded border border-emerald-600/30 bg-emerald-600/5 px-1.5 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-400'>{id}</code>)}</div>}
                  <ul className='mt-3 space-y-2'>{selected.body.llm.output.root_causes.map(cause => <li key={cause.cause} className='border-l-2 pl-3'><strong className='text-sm font-medium'>{cause.cause}</strong><span className='mt-0.5 block text-xs leading-relaxed text-muted-foreground'>{cause.reasoning}</span></li>)}</ul>
                </> : <p className='mt-2 text-xs text-muted-foreground'>本次建议由确定性规则生成，LLM 未参与（{llmReasonLabels[selected.body.llm.reason ?? ''] ?? selected.body.llm.reason ?? '未调用'}）。</p>}
              </div>}

              <div className='border-b px-5 py-4'>
                <div className='rounded-lg border bg-muted/40 p-4'>
                  <span className='block text-xs text-muted-foreground'>条件性利润改善情景</span>
                  <strong className='mt-1 block text-2xl font-semibold tracking-tight'>{money(selected.body.expected_impact.profit_improvement_min)} — {money(selected.body.expected_impact.profit_improvement_max)}</strong>
                  <small className='text-xs text-muted-foreground'>未来 {selected.body.expected_impact.horizon_days} 天 · 非实际收益或保证值</small>
                  <p className='mt-2 border-t pt-2 text-xs leading-relaxed text-muted-foreground'>{selected.body.expected_impact.explanation}</p>
                </div>
              </div>

              <div className='border-b px-5 py-4'>
                <div className='flex items-center gap-2'><h3 className='text-sm font-medium'>待执行的模拟动作</h3><Badge variant='outline' className='text-[10px]'>SIMULATION ONLY</Badge></div>
                {selected.action.type === 'adjust_ad_budget' ? <div className='mt-3 flex items-center gap-6 rounded-lg border bg-muted/30 p-4'>
                  <div><span className='text-xs text-muted-foreground'>参考日预算</span><strong className='mt-1 block text-xl font-semibold tabular-nums'>{money(selected.action.baseline_daily_budget)}</strong></div>
                  <ArrowRight className='size-4 text-muted-foreground' />
                  <div><span className='text-xs text-muted-foreground'>模拟新预算</span><strong className='mt-1 block text-xl font-semibold tabular-nums'>{money(selected.action.proposed_daily_budget)}</strong></div>
                </div> : <p className='mt-2 text-sm text-muted-foreground'>为 {selected.action.sku} 创建成本核查任务预览。</p>}
                <p className='mt-2 text-xs text-muted-foreground'>目标 SKU：{selected.action.sku}。该动作只写入模拟记录，不会调用广告、采购或支付系统。</p>
              </div>

              <div className='grid grid-cols-1 gap-6 border-b px-5 py-4 sm:grid-cols-2'>
                <div><h3 className='flex items-center gap-1.5 text-sm font-medium'><TriangleAlert className='size-3.5' />风险与假设</h3><ul className='mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted-foreground'>{[...selected.body.risks, ...selected.body.assumptions].map(value => <li key={value}>{value}</li>)}</ul></div>
                <div><h3 className='flex items-center gap-1.5 text-sm font-medium'><Database className='size-3.5' />待补充信息</h3><ul className='mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted-foreground'>{selected.body.missing_information.map(value => <li key={value}>{value}</li>)}</ul></div>
              </div>

              <div className='px-5 py-4'>
                <div className='flex flex-wrap items-center gap-2'>
                  <ShieldCheck className='size-4' />
                  <strong className='text-sm font-medium'>人工审批与模拟执行</strong>
                  <Badge variant='outline' className='text-[10px]'>SIMULATION ONLY</Badge>
                </div>
                {!isApprover && <p className='mt-2 text-xs text-muted-foreground'>当前为经营分析师。请在右上角切换“审批负责人”进行审批与模拟执行。</p>}
                {selected.status === 'pending_approval' && isApprover && <>
                  <label className='mt-3 flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'>审批意见
                    <Textarea aria-label='审批意见' value={reason} onChange={event => setReason(event.target.value)} rows={2} />
                  </label>
                  <div className='mt-3 flex justify-end gap-2'>
                    <Button variant='destructive' onClick={() => decide('reject')} disabled={!!busy || reason.trim().length < 2}><X className='size-4' />拒绝建议</Button>
                    <Button onClick={() => decide('approve')} disabled={!!busy || reason.trim().length < 2}><Check className='size-4' />批准建议</Button>
                  </div>
                </>}
                {selected.approval_reason && <p className='mt-2 text-xs text-muted-foreground'>审批意见：{selected.approval_reason}</p>}
                {selected.body.approval && <div className='reveal mt-3 rounded-lg border bg-muted/25 p-3'>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Fingerprint className='size-4 text-primary' />
                    <strong className='text-xs font-medium'>审批版本快照</strong>
                    <Badge variant='outline' className='text-[10px] tabular-nums'>v{selected.body.approval.version}</Badge>
                  </div>
                  <div className='mt-2 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2'>
                    <div><span className='block text-muted-foreground'>绑定时间</span><strong className='mt-0.5 block font-medium tabular-nums'>{dateTime(selected.body.approval.captured_at)}</strong></div>
                    <div><span className='block text-muted-foreground'>账本指纹</span><code className='mt-0.5 block font-medium' title={selected.body.approval.ledger_hash}>{selected.body.approval.ledger_hash.slice(0, 12)}</code></div>
                    <div><span className='block text-muted-foreground'>动作指纹</span><code className='mt-0.5 block font-medium' title={selected.body.approval.action_hash}>{selected.body.approval.action_hash.slice(0, 12)}</code></div>
                    <div><span className='block text-muted-foreground'>当前净销售额 / 净利润</span><strong className='mt-0.5 block font-medium tabular-nums'>{money(selected.body.approval.current_totals.net_sales)} / {money(selected.body.approval.current_totals.net_profit)}</strong></div>
                  </div>
                  <p className='mt-2 border-t pt-2 text-[11px] leading-relaxed text-muted-foreground'>执行前会重新核对账本与动作参数 任一变化都会拒绝模拟执行</p>
                </div>}
                {isApprover && ['approved', 'executed', 'execution_failed'].includes(selected.status) && <Button className='mt-3' onClick={execute} disabled={!!busy}>{busy === 'execute' ? <LoaderCircle className='size-4 animate-spin' /> : <Play className='size-4' />}{selected.status === 'approved' ? '模拟执行已批准动作' : '再次请求 · 验证幂等'}</Button>}
                <div className='mt-4 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground'>{['pending_approval', 'approved', 'executing', 'executed'].map((value, index) => <span key={value} className={cn('flex items-center gap-1.5', selected.status === value && 'font-medium text-foreground')}>{index > 0 && <ArrowRight className='size-3' />}{statusLabels[value]}</span>)}</div>
              </div>

              {(selected.execution || execution?.recommendation_id === selected.id) && <ExecutionResult result={execution?.recommendation_id === selected.id ? execution : selected.execution!} />}
            </Card> : <Card className='p-5'><Empty title='选择一条建议' text='建议会展示统计范围、证据、影响情景与审批状态。' /></Card>}
          </div>}

          {page === 'knowledge' && <div className='grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]'>
            <Card className='gap-0 self-start py-0'>
              <SectionHeading title='运营知识库' icon={<BookOpen className='size-4' />} description={demo?.knowledge ? `${demo.knowledge.documents} 篇文档 · ${demo.knowledge.embedding_model} · ${demo.knowledge.embedding_provider}` : '离线哈希向量 + 词法混合检索'} action={<Badge variant='outline'>RAG</Badge>} />
              <CardContent className='px-5 py-4'>
                <form className='flex flex-col gap-2 sm:flex-row sm:items-end' onSubmit={searchKnowledge}>
                  <label className='flex flex-1 flex-col gap-1.5 text-xs font-medium text-muted-foreground'>检索问题
                    <Input aria-label='知识库检索' value={knowledgeQuery} onChange={event => setKnowledgeQuery(event.target.value)} placeholder='例如：广告预算下调需要谁审批？' />
                  </label>
                  <Button type='submit' disabled={!!busy || knowledgeQuery.trim().length < 2}>{busy === 'knowledge-search' ? <LoaderCircle className='size-4 animate-spin' /> : <Search className='size-4' />}检索知识</Button>
                </form>
                <div className='mt-4'>{knowledgeResults ? <KnowledgeHits knowledge={knowledgeResults} /> : <Empty title='从运营规则里找答案' text='检索结果会返回可引用的 chunk_id；低于阈值的查询明确显示“无可靠答案”。' />}</div>
              </CardContent>
            </Card>

            <Card className='gap-0 self-start py-0'>
              <SectionHeading title='知识文档' description='审批负责人可新增或停用；停用不清理审计历史' action={<Badge variant='secondary' className='tabular-nums'>{documents.length}</Badge>} />
              <CardContent className='px-5 py-4'>
                {!documents.length ? <Empty title='知识库为空' text='以审批负责人身份添加第一篇 SOP。' /> : <div className='knowledge-doc-list space-y-2'>{documents.map(doc => <article key={doc.id} className='flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5'>
                  <div className='min-w-0'><strong className='block truncate text-sm font-medium'>{doc.title}</strong><small className='mt-0.5 block break-all text-xs text-muted-foreground'>{doc.source} · {doc.version} · {doc.chunk_count} 个片段</small></div>
                  {isApprover && <button type='button' className='shrink-0 text-xs font-medium text-destructive underline-offset-4 hover:underline' onClick={() => removeKnowledgeDocument(doc.id)} disabled={!!busy}>停用</button>}
                </article>)}</div>}
                {isApprover && <form className='mt-4 space-y-3 border-t pt-4' onSubmit={addKnowledgeDocument}>
                  <h3 className='flex items-center gap-2 text-sm font-medium'>添加知识文档<Badge variant='outline' className='text-[10px]'>APPROVER ONLY</Badge></h3>
                  <div className='grid gap-3 sm:grid-cols-[1fr_100px]'>
                    <label className='flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'>标题<Input aria-label='知识标题' value={knowledgeDraft.title} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, title: event.target.value })} placeholder='例如：广告预算调整规则' /></label>
                    <label className='flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'>版本<Input aria-label='知识版本' value={knowledgeDraft.version} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, version: event.target.value })} /></label>
                  </div>
                  <label className='flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'>来源<Input aria-label='知识来源' value={knowledgeDraft.source} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, source: event.target.value })} /></label>
                  <label className='flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'>正文<Textarea aria-label='知识正文' rows={5} value={knowledgeDraft.content} onChange={event => setKnowledgeDraft({ ...knowledgeDraft, content: event.target.value })} placeholder={'# 适用范围\n...（支持 Markdown 标题分段）'} /></label>
                  <Button type='submit' disabled={!!busy || knowledgeDraft.title.trim().length < 2 || knowledgeDraft.content.trim().length < 10}>添加知识文档</Button>
                </form>}
              </CardContent>
            </Card>
          </div>}

          {page === 'audit' && <Card className='gap-0 overflow-hidden py-0'>
            <SectionHeading title='决策与执行轨迹' description={`最近 ${audits.length} 条事件 · 记录批准、拒绝、模拟、失败与重复请求`} action={<Badge variant='outline' className='gap-1'><Fingerprint className='size-3' />请求级追踪</Badge>} />
            <form className='flex flex-wrap items-end gap-2 border-b px-5 py-4' onSubmit={event => { event.preventDefault(); setAuditFilter(auditQuery.trim()) }}>
              <label className='flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'>请求 ID<Input aria-label='按请求 ID 过滤' placeholder='精确匹配请求 ID' value={auditQuery} onChange={event => setAuditQuery(event.target.value)} /></label>
              <Button variant='secondary' type='submit'>筛选</Button>
              {auditFilter && <Button variant='ghost' type='button' onClick={() => { setAuditQuery(''); setAuditFilter('') }}>清除筛选</Button>}
              <span className='ml-auto text-xs text-muted-foreground'>{auditFilter ? `当前筛选：${auditFilter}` : '显示最近 100 条；可按请求 ID 精确追踪'}</span>
            </form>
            {!audits.length ? <div className='p-5'><Empty title={auditFilter ? '没有匹配的事件' : '审计日志为空'} text={auditFilter ? `请求 ID ${auditFilter} 没有对应审计事件，请检查后重试。` : '启动一次分析，系统会自动记录后续决策与执行事件。'} /></div> : <Table>
              <TableHeader><TableRow><TableHead>时间 / 本地显示</TableHead><TableHead>操作人</TableHead><TableHead>动作</TableHead><TableHead>对象</TableHead><TableHead>结果</TableHead><TableHead>关联请求 ID</TableHead></TableRow></TableHeader>
              <TableBody>{audits.map(item => <TableRow key={item.id}>
                <TableCell>{dateTime(item.created_at)}</TableCell>
                <TableCell><span className='flex items-center gap-1.5'>{item.actor === 'demo-approver' ? <ShieldCheck className='size-3.5' /> : <Activity className='size-3.5' />}{item.actor}</span></TableCell>
                <TableCell className='whitespace-normal'><strong className='font-medium'>{item.action}</strong><details className='mt-1'><summary className='cursor-pointer text-xs text-muted-foreground'>事件详情</summary><pre className='mt-1 max-h-60 max-w-[360px] overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-[11px] leading-relaxed'>{JSON.stringify(item.details, null, 2)}</pre></details></TableCell>
                <TableCell><span className='block max-w-[140px] break-all font-mono text-[11px] text-muted-foreground' title={item.object_id}>{item.object_id.slice(0, 12)}</span><small className='text-xs text-muted-foreground'>{item.object_type}</small></TableCell>
                <TableCell><span className={cn('text-xs font-medium', item.outcome === 'success' ? 'text-emerald-600 dark:text-emerald-400' : item.outcome === 'denied' || item.outcome === 'failure' ? 'text-destructive' : 'text-muted-foreground')}>{item.outcome === 'success' ? '✓ 成功' : item.outcome === 'replayed' ? '↻ 幂等返回' : item.outcome === 'denied' ? '⊘ 已拦截' : item.outcome === 'degraded' ? '⚠ 已降级' : item.outcome === 'failure' ? '× 失败' : item.outcome}</span></TableCell>
                <TableCell><code className='block max-w-[140px] break-all font-mono text-[11px] text-muted-foreground'>{item.request_id}</code></TableCell>
              </TableRow>)}</TableBody>
            </Table>}
          </Card>}
        </div>
      </main>

      <footer className='mx-auto flex w-full max-w-[1600px] flex-col gap-2 border-t px-4 py-4 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between md:px-6'>
        <span>ProfitOps AI <span className='text-border'>/</span> 从数据到决策的完整闭环</span>
        <span className='flex items-center gap-1.5'><ShieldCheck className='size-3' />所有执行均为模拟 · {demo?.data_range ? '数据已接入' : '等待初始化'}</span>
      </footer>
    </div>
  </div>
}

function LLMPanel({ llm, config }: { llm?: LLMRun | null; config?: LLMConfig }) {
  const status = llm?.status
  const output = llm?.output
  const variant = status === 'llm_success' ? 'success' : status === 'deterministic_fallback' ? 'warning' : status === 'llm_failed' ? 'danger' : 'secondary'
  return <Card className='gap-0 py-0'>
    <SectionHeading title='DeepSeek 根因分析' icon={<Sparkles className='size-4' />} description={config?.configured ? `真实模型 ${config.model}` : '未配置 API Key · 确定性规则'} action={<Badge variant={variant}>{status ? llmStatusLabels[status] ?? status : '等待分析'}</Badge>} />
    {!llm ? <p className='px-5 py-4 text-sm leading-relaxed text-muted-foreground'>启动一次异常分析后，这里会显示模型解释、证据引用与经营建议。{config?.configured ? '当前已配置 DeepSeek。' : '当前未配置 LLM，将使用确定性规则。'}</p> : <>
      <div className='grid grid-cols-2 gap-3 px-5 py-4 sm:grid-cols-4'>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>模型<strong className='break-words text-sm font-medium text-foreground'>{llm.model}</strong></span>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>耗时<strong className='text-sm font-medium text-foreground'>{llm.latency_ms == null ? '—' : `${llm.latency_ms.toFixed(1)} ms`}</strong></span>
        {llm.usage && <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>Token<strong className='text-sm font-medium text-foreground'>{llm.usage.total_tokens ?? '—'}<small className='block text-[11px] font-normal text-muted-foreground'>输入 {llm.usage.prompt_tokens} / 输出 {llm.usage.completion_tokens ?? '—'}</small></strong></span>}
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>尝试<strong className='text-sm font-medium text-foreground'>{llm.attempts}</strong></span>
      </div>
      {output ? <>
        <div className='relative mx-5 rounded-lg bg-muted/60 p-4'>
          <span className='text-[11px] font-medium tracking-wide text-muted-foreground'>LLM SUMMARY</span>
          <p className='mt-1.5 pr-20 text-sm leading-relaxed'>{output.summary}</p>
          <Badge variant='outline' className='absolute top-3 right-3 bg-background'>置信度 {(output.confidence * 100).toFixed(0)}%</Badge>
        </div>
        <div className='border-t px-5 py-4'>
          <h3 className='text-xs font-medium text-muted-foreground'>根因分析</h3>
          <div className='mt-2 space-y-2'>{output.root_causes.map(cause => <div className='rounded-lg border p-3' key={cause.cause}>
            <strong className='text-sm font-medium'>{cause.cause}</strong>
            <p className='mt-1 text-xs leading-relaxed text-muted-foreground'>{cause.reasoning}</p>
            <div className='mt-2 flex flex-wrap gap-1'>{cause.evidence_ids.map(id => <code key={id} className='rounded border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground'>{id}</code>)}</div>
          </div>)}</div>
        </div>
        <div className='border-t px-5 py-4'>
          <h3 className='text-xs font-medium text-muted-foreground'>经营建议</h3>
          <div className='mt-2 space-y-2'>{output.recommendations.map(item => <div className='rounded-lg border p-3' key={item.action}>
            <div className='flex items-start justify-between gap-2'>
              <strong className='text-sm font-medium leading-snug'>{item.action}</strong>
              <Badge variant={item.priority === 'high' ? 'danger' : item.priority === 'medium' ? 'warning' : 'secondary'} className='text-[10px] uppercase'>{item.priority}</Badge>
            </div>
            <p className='mt-1 text-xs leading-relaxed text-muted-foreground'>{item.rationale}</p>
            <div className='mt-2 flex flex-wrap gap-1'>{item.evidence_ids.map(id => <code key={id} className='rounded border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground'>{id}</code>)}</div>
          </div>)}</div>
        </div>
        <div className='border-t px-5 py-4'>
          <h3 className='text-xs font-medium text-muted-foreground'>预期影响</h3>
          <p className='mt-1 text-sm leading-relaxed text-muted-foreground'>{output.expected_impact.explanation}</p>
          <span className='mt-1 block text-xs text-muted-foreground/80'>{output.expected_impact.horizon_days} 天 · 条件情景，不是收益承诺</span>
        </div>
        {(output.risks.length > 0 || output.missing_information.length > 0) && <div className='border-t px-5 py-4'>
          <h3 className='text-xs font-medium text-muted-foreground'>风险与待补充</h3>
          <ul className='mt-1 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted-foreground'>{output.risks.map(value => <li key={value}>{value}</li>)}{output.missing_information.map(value => <li key={value}>{value}</li>)}</ul>
        </div>}
        <div className='border-t px-5 py-4'>
          <h3 className='text-xs font-medium text-muted-foreground'>证据引用</h3>
          <div className='mt-1.5 flex flex-wrap gap-1'>{output.evidence_references.map(id => <code key={id} className='rounded border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground'>{id}</code>)}</div>
        </div>
        {(output.knowledge_references?.length ?? 0) > 0 && <div className='border-t px-5 py-4'>
          <h3 className='text-xs font-medium text-muted-foreground'>知识引用</h3>
          <div className='mt-1.5 flex flex-wrap gap-1'>{output.knowledge_references!.map(id => <code key={id} className='rounded border border-emerald-600/30 bg-emerald-600/5 px-1.5 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-400'>{id}</code>)}</div>
        </div>}
      </> : <div className='border-t px-5 py-4 text-sm text-muted-foreground'>
        <p><strong className='font-medium text-foreground'>{status ? llmStatusLabels[status] ?? status : 'LLM 未参与'}</strong>{llm.reason ? `：${llmReasonLabels[llm.reason] ?? llm.reason}` : ''}</p>
        {llm.error && <p className='mt-1 text-xs'>错误码 {llm.error.code} · {llm.error.message}</p>}
        <p className='mt-1 text-xs'>{status === 'not_requested' ? '本次分析未调用 LLM；展示的是确定性计算结果。' : '本次结论、证据与建议由确定性规则引擎生成，不是 LLM 输出。'}</p>
      </div>}
    </>}
  </Card>
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
  const variant = llm.status === 'llm_success' ? 'success' : llm.status === 'deterministic_fallback' ? 'warning' : llm.status === 'llm_failed' ? 'danger' : 'secondary'
  return <Card role='region' aria-label='Agent 执行轨迹' className='gap-0 py-0'>
    <SectionHeading title='Agent 执行轨迹' icon={<Activity className='size-4' />} description={native ? '原生 Function Calling · 只读工具循环' : '本次未使用原生工具循环'} action={<Badge variant={variant}>{llmStatusLabels[llm.status] ?? llm.status}</Badge>} />
    {!native ? <p className='px-5 py-4 text-sm text-muted-foreground'>{llm.status === 'not_requested' ? '本次分析未触发 LLM，因此没有模型轮次或工具调用。' : '本次分析走单次 JSON 路径，未产生原生工具轮次。'}</p> : <>
      <div className='grid grid-cols-2 gap-3 border-b px-5 py-4 sm:grid-cols-3 xl:grid-cols-6'>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>模型<strong className='break-words text-sm font-medium text-foreground'>{llm.model}</strong></span>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>轮次<strong className='text-sm font-medium text-foreground'>{rounds.length}</strong></span>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>工具调用<strong className='text-sm font-medium text-foreground'>{calls.length}</strong></span>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>总 Token<strong className='text-sm font-medium text-foreground'>{llm.usage?.total_tokens ?? '—'}</strong></span>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>总耗时<strong className='text-sm font-medium text-foreground'>{llm.latency_ms == null ? '—' : `${llm.latency_ms.toFixed(1)} ms`}</strong></span>
        <span className='flex flex-col gap-0.5 text-xs text-muted-foreground'>终止原因<strong className='text-sm font-medium text-foreground'>{terminationLabels[llm.termination_reason ?? ''] ?? llm.termination_reason ?? '—'}</strong></span>
      </div>
      {!rounds.length ? <p className='px-5 py-4 text-sm text-muted-foreground'>没有可展示的模型轮次。</p> : <ol className='space-y-3 px-5 py-4'>
        {rounds.map(round => {
          const roundCalls = calls.filter(call => call.round === round.round)
          return <li className='overflow-hidden rounded-lg border bg-muted/30' key={round.round}>
            <div className='flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-2 text-xs text-muted-foreground'>
              <span className='font-semibold tracking-wide text-foreground'>ROUND {round.round}</span>
              <span className='rounded-full border bg-background px-2 py-0.5 font-mono text-[11px]'>{round.finish_reason ?? '—'}</span>
              <span>{round.tool_call_count} 次工具</span>
              <span>{round.latency_ms.toFixed(1)} ms</span>
              <span>{round.usage?.total_tokens == null ? 'Token —' : `${round.usage.total_tokens} tokens`}</span>
            </div>
            {roundCalls.length ? <div className='space-y-2 p-2.5'>{roundCalls.map(call => <div className={cn('rounded-md border-l-2 bg-background p-2.5', call.ok ? 'border-l-muted-foreground/30' : 'border-l-destructive bg-destructive/5')} key={call.tool_call_id}>
              <div className='flex flex-wrap items-center gap-2'>
                <code className='rounded border bg-muted px-1.5 py-0.5 text-[11px]'>{call.name}</code>
                <span className={cn('text-[11px] font-medium', call.ok ? 'text-emerald-600 dark:text-emerald-400' : 'font-semibold text-destructive')}>{call.ok ? '✓ 成功' : '× 失败'}</span>
                <span className='text-[11px] text-muted-foreground'>{call.latency_ms.toFixed(1)} ms</span>
              </div>
              <p className='mt-1.5 flex gap-2 text-xs text-muted-foreground'><span className='shrink-0 text-[11px]'>参数</span><span className='break-words'>{toolArgumentSummary(call.arguments)}</span></p>
              <p className='mt-1 flex gap-2 text-xs text-muted-foreground'><span className='shrink-0 text-[11px]'>结果</span><span className='break-words'>{bounded(call.result_summary, 160)}</span></p>
              {!call.ok && <p className='mt-1 text-xs text-destructive'>错误码：{call.error_code ?? '未记录'}</p>}
            </div>)}</div> : <p className='px-3 py-2 text-xs text-muted-foreground'>本轮无工具调用</p>}
          </li>
        })}
      </ol>}
      {llm.error && <p className='border-t px-5 py-3 text-xs text-destructive'>本次运行错误：{llm.error.code} · {bounded(llm.error.message, 180)}</p>}
    </>}
  </Card>
}

function KnowledgeHits({ knowledge, showCited = false }: { knowledge: KnowledgeSearch | KnowledgeContext; showCited?: boolean }) {
  if (knowledge.no_answer) return <div className='flex gap-3 rounded-lg border border-amber-200 bg-amber-50/60 p-4 text-sm dark:border-amber-900/50 dark:bg-amber-950/20'><TriangleAlert className='mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400' /><div><strong className='font-medium'>无可靠答案</strong><p className='mt-0.5 text-xs leading-relaxed text-muted-foreground'>没有片段达到相似度阈值；系统没有把低相关结果交给模型引用。</p></div></div>
  return <div className='space-y-2'>{knowledge.hits.map(hit => {
    const cited = 'cited' in hit && hit.cited
    return <article className='rounded-lg border bg-muted/30 p-4' key={hit.chunk_id}>
      <div className='flex flex-wrap items-center gap-2'>
        <strong className='text-sm font-medium'>{hit.title}</strong>
        {showCited && <Badge variant={cited ? 'success' : 'outline'} className='text-[10px]'>{cited ? '✓ LLM 已引用' : '仅检索参考'}</Badge>}
        <span className='ml-auto text-xs tabular-nums text-muted-foreground'>{hit.score.toFixed(3)}</span>
      </div>
      <div className='mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground'><span>{hit.source}</span><span>{hit.version}</span>{hit.heading && <span>{hit.heading}</span>}</div>
      <p className='mt-2 text-sm leading-relaxed text-muted-foreground'>{bounded(hit.content, 360)}</p>
      <code className='mt-1 block break-all text-[11px] text-muted-foreground/80'>{hit.chunk_id}</code>
    </article>
  })}</div>
}

function KnowledgePanel({ knowledge, showCited = false, className }: { knowledge?: KnowledgeSearch | KnowledgeContext | null; showCited?: boolean; className?: string }) {
  if (!knowledge) return null
  return <Card className={cn('gap-0 py-0', className)}>
    <SectionHeading title='知识库参考' icon={<BookOpen className='size-4' />} description={knowledge.no_answer ? '未找到可靠匹配' : `${knowledge.hits.length} 个片段 · ${knowledge.embedding_model} · ${knowledge.retrieval_ms.toFixed(1)} ms`} action={<Badge variant={knowledge.no_answer ? 'warning' : 'success'}>{knowledge.no_answer ? '无可靠答案' : '已检索'}</Badge>} />
    <div className='px-5 py-4'><KnowledgeHits knowledge={knowledge} showCited={showCited} /></div>
    <p className='border-t px-5 py-3 text-xs leading-relaxed text-muted-foreground'>知识片段作为不可信数据传给模型；只有本次检索结果中的 chunk_id 才能通过引用校验。</p>
  </Card>
}

function ExecutionResult({ result }: { result: Execution }) {
  const failed = result.status === 'execution_failed'
  return <div className={cn('m-5 rounded-lg border p-4', failed ? 'border-destructive/30 bg-destructive/5' : 'border-emerald-600/20 bg-emerald-600/5')}>
    <div className='flex items-center gap-2'>
      <CheckCircle2 className={cn('size-5', failed ? 'text-destructive' : 'text-emerald-600 dark:text-emerald-400')} />
      <h3 className='flex items-center gap-2 text-sm font-medium'>{failed ? '模拟执行失败' : '模拟执行结果'}<Badge variant='outline' className='text-[10px]'>SIMULATION</Badge></h3>
    </div>
    <p className='mt-2 text-sm text-muted-foreground'>{result.error?.message ?? result.result?.message}</p>
    {result.result?.before && <div className='mt-3 flex items-center gap-6 rounded-lg border bg-background/60 p-3'>
      <div><span className='text-xs text-muted-foreground'>参考日预算</span><strong className='mt-0.5 block text-xl font-semibold tabular-nums'>{money(result.result.before.daily_budget)}</strong></div>
      <ArrowRight className='size-4 text-muted-foreground' />
      <div><span className='text-xs text-muted-foreground'>模拟新预算</span><strong className='mt-0.5 block text-xl font-semibold tabular-nums'>{money(result.result.after!.daily_budget)}</strong></div>
    </div>}
    <p className='mt-2 font-mono text-[11px] text-muted-foreground'>执行 ID：{result.execution_id}</p>
    <div className='mt-2 flex flex-wrap gap-4 text-xs text-muted-foreground'>
      <span className='flex items-center gap-1'><Check className='size-3' />外部调用 {result.result?.external_calls ?? 0} 次</span>
      <span className='flex items-center gap-1'><Fingerprint className='size-3' />{result.replayed ? '命中幂等，复用原执行记录' : '唯一执行记录已保存'}</span>
    </div>
  </div>
}

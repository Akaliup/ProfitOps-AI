import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowRight, CheckCircle2, Download, FileSpreadsheet,
  LoaderCircle, RefreshCw, ShieldCheck, Upload, XCircle,
} from 'lucide-react'
import { ApiError, api } from '../../api'
import { money } from '../../format'
import type { ImportSummary } from '../../types'
import { cn } from '../../lib/utils'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card'

type Kind = 'orders' | 'operations' | 'ads' | 'inventory'

const issueLabels: Record<string, string> = {
  missing_money: '金额缺失', invalid_money: '金额格式', money_out_of_range: '金额超范围',
  invalid_integer: '整数格式', invalid_boolean: '布尔值', invalid_date: '日期格式',
  date_out_of_range: '日期范围', missing_store: '缺少店铺', unknown_store: '未知店铺',
  missing_sku: '缺少 SKU', unknown_sku: '未知 SKU', missing_order_id: '缺少订单号',
  invalid_order_id: '订单号过长', duplicate_in_file: '文件内重复', order_conflict: '订单冲突',
  operation_conflict: '经营记录冲突', discount_exceeds_sales: '折扣退款超销售额',
  import_header_invalid: '表头不完整', import_encoding_invalid: '编码错误',
}

const statusMeta = {
  ready: { label: '校验通过', tone: 'success' as const, icon: CheckCircle2 },
  blocked: { label: '未通过校验', tone: 'danger' as const, icon: XCircle },
  committed: { label: '已写入账本', tone: 'success' as const, icon: ShieldCheck },
  noop: { label: '重复文件 未写入', tone: 'info' as const, icon: CheckCircle2 },
}

const kindMeta: Record<Kind, { label: string; hint: string }> = {
  ads: { label: '广告活动', hint: '活动明细 按店铺 SKU 日期汇总入账' },
  inventory: { label: '库存快照', hint: '预留 在途 到货时间 采购周期 MOQ 整箱量' },
  orders: { label: '订单明细', hint: '销售额 折扣 退款 采购 履约 平台费' },
  operations: { label: '每日经营', hint: '广告支出 期末库存 在途库存 完整性' },
}

export function ImportPage({ token, isApprover, onImported }: {
  token: string
  isApprover: boolean
  onImported: () => void
}) {
  const [kind, setKind] = useState<Kind>('orders')
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  const [mapping, setMapping] = useState('')
  const [summary, setSummary] = useState<ImportSummary | null>(null)
  const [batches, setBatches] = useState<ImportSummary[]>([])
  const [busy, setBusy] = useState<'preview' | 'commit' | 'template' | 'list' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  const loadBatches = useCallback(async () => {
    setBusy('list')
    try { setBatches((await api.imports(token)).items) }
    catch (value) { setError(value instanceof ApiError ? value.message : '读取导入记录失败') }
    finally { setBusy('') }
  }, [token])

  useEffect(() => { void loadBatches() }, [loadBatches])

  async function pick(next: File | null) {
    if (!next) return
    if (!next.name.toLowerCase().endsWith('.csv')) { setError('只支持 CSV 文件'); return }
    if (next.size > 5 * 1024 * 1024) { setError('文件不能超过 5 MB'); return }
    setError(''); setNotice(''); setSummary(null); setFile(next)
    try { setText(await next.text()) }
    catch { setError('无法读取文件内容') }
  }

  async function preview() {
    if (!file || !text) { setError('请先选择 CSV 文件'); return }
    setBusy('preview'); setError(''); setNotice('')
    try {
      setSummary(await api.previewImport(token, kind, text, file.name, mapping))
    } catch (value) {
      if (value instanceof ApiError && value.payload) {
        const body = value.payload as { error?: { details?: { summary?: ImportSummary } } }
        const blocked = body.error?.details?.summary
        if (blocked) setSummary(blocked)
      }
      setError(value instanceof ApiError ? value.message : '校验失败')
    } finally { setBusy('') }
  }

  async function commit() {
    if (!file || !text) return
    setBusy('commit'); setError(''); setNotice('')
    try {
      const result = await api.commitImport(token, kind, text, file.name, mapping)
      setSummary(result)
      setNotice(result.status === 'blocked' ? '整批未写入 请查看错误行' : result.status === 'noop' ? '该文件已导入过 账本没有重复写入' : `已写入 ${result.accepted_rows} 行 已保存来源与批次`)
      onImported()
      await loadBatches()
    } catch (value) {
      if (value instanceof ApiError && value.payload) {
        const body = value.payload as { error?: { details?: { summary?: ImportSummary } } }
        const blocked = body.error?.details?.summary
        if (blocked) setSummary(blocked)
      }
      setError(value instanceof ApiError ? value.message : '导入失败')
    } finally { setBusy('') }
  }

  async function downloadTemplate() {
    setBusy('template'); setError('')
    try {
      const response = await fetch(api.importTemplateUrl(kind), { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) throw new Error('模板下载失败')
      const url = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `${kind}-template.csv`; anchor.click()
      URL.revokeObjectURL(url)
    } catch (value) {
      setError(value instanceof Error ? value.message : '模板下载失败')
    } finally { setBusy('') }
  }

  const meta = summary ? statusMeta[summary.status] : null
  const StatusIcon = meta?.icon
  const totals = summary?.totals

  return <div className='space-y-6'>
    {error && <div className='reveal flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive' role='alert'>
      <AlertTriangle className='mt-0.5 size-4 shrink-0' />
      <span className='flex-1 break-words'>{error}</span>
      <button type='button' className='rounded-sm opacity-70 transition-opacity hover:opacity-100' aria-label='关闭错误' onClick={() => setError('')}><XCircle className='size-4' /></button>
    </div>}
    {notice && <div className='reveal flex items-start gap-3 rounded-lg border border-emerald-600/20 bg-emerald-600/5 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-400' role='status'>
      <CheckCircle2 className='mt-0.5 size-4 shrink-0' />
      <span className='flex-1 break-words'>{notice}</span>
    </div>}

    {kind !== 'operations' && <label className='block text-sm'>字段映射 可选 JSON 标准字段名对应 CSV 列名<textarea aria-label='字段映射' className='mt-2 block w-full rounded border p-2' placeholder={'{"store_id":"店铺","sku":"商品编号"}'} value={mapping} onChange={e => { setMapping(e.target.value); setSummary(null) }} /></label>}
    <div className='grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]'>
      <Card className='reveal gap-0 overflow-hidden py-0'>
        <CardHeader className='border-b bg-muted/30 px-6 py-5'>
          <div className='flex flex-wrap items-center justify-between gap-3'>
            <div className='space-y-1'>
              <CardTitle className='flex items-center gap-2 text-base'><FileSpreadsheet className='size-4 text-sky-600 dark:text-sky-400' />导入经营数据</CardTitle>
              <CardDescription>先校验 再写入 一次文件要么全部通过 要么整批拒绝</CardDescription>
            </div>
            <Button variant='outline' size='sm' onClick={() => void downloadTemplate()} disabled={busy === 'template'}>
              {busy === 'template' ? <LoaderCircle className='size-4 animate-spin' /> : <Download className='size-4' />}CSV 模板
            </Button>
          </div>
        </CardHeader>
        <CardContent className='space-y-5 px-6 py-5'>
          <div className='flex flex-wrap gap-2' role='group' aria-label='导入类型'>
            {(Object.keys(kindMeta) as Kind[]).map(item => <button
              key={item} type='button' aria-pressed={kind === item}
              onClick={() => { setKind(item); setSummary(null); setFile(null); setText('') }}
              className={cn(
                'group flex-1 rounded-lg border px-4 py-3 text-left transition-all duration-300',
                kind === item
                  ? 'border-sky-600/40 bg-sky-50 shadow-sm dark:border-sky-400/40 dark:bg-sky-950/30'
                  : 'hover:-translate-y-0.5 hover:border-sky-600/30 hover:shadow-sm',
              )}
            >
              <span className='flex items-center gap-2 text-sm font-medium'>
                {kindMeta[item].label}
                {kind === item && <Badge variant='info' className='ml-auto'>已选择</Badge>}
              </span>
              <span className='mt-1 block text-xs text-muted-foreground'>{kindMeta[item].hint}</span>
            </button>)}
          </div>

          <div
            role='button' tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); inputRef.current?.click() } }}
            onDragEnter={event => { event.preventDefault(); dragDepth.current += 1; setDragging(true) }}
            onDragOver={event => { event.preventDefault(); setDragging(true) }}
            onDragLeave={event => {
              event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1)
              if (dragDepth.current === 0) setDragging(false)
            }}
            onDrop={event => {
              event.preventDefault(); dragDepth.current = 0; setDragging(false)
              void pick(event.dataTransfer.files[0] ?? null)
            }}
            data-dragging={dragging}
            className={cn(
              'dropzone flex min-h-[190px] cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-8 text-center outline-none',
              'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
              dragging ? 'border-sky-600 bg-sky-50/70 dark:border-sky-400 dark:bg-sky-950/30' : 'hover:border-sky-600/50 hover:bg-muted/40',
            )}
          >
            <span className={cn('flex size-12 items-center justify-center rounded-full bg-muted transition-transform duration-300', dragging && 'scale-110')}>
              <Upload className='size-5 text-sky-600 dark:text-sky-400' />
            </span>
            <span className='text-sm font-medium'>{file ? file.name : '拖入 CSV 文件 或点击选择'}</span>
            <span className='text-xs text-muted-foreground'>
              {file ? `${(file.size / 1024).toFixed(1)} KB · 已读取 ${text.split('\n').length - 1} 行` : `UTF-8 编码 · 单次最多 5000 行 · 5 MB · 当前类型 ${kindMeta[kind].label}`}
            </span>
          </div>
          <input ref={inputRef} type='file' accept='.csv,text/csv' className='sr-only' aria-label='选择 CSV 文件' onChange={event => void pick(event.target.files?.[0] ?? null)} />

          <div className='flex flex-wrap items-center gap-2'>
            <Button onClick={() => void preview()} disabled={!file || busy === 'preview' || busy === 'commit'}>
              {busy === 'preview' ? <LoaderCircle className='size-4 animate-spin' /> : <ShieldCheck className='size-4' />}校验文件
            </Button>
            <Button variant='outline' onClick={() => { setFile(null); setText(''); setSummary(null); setError(''); setNotice('') }}>
              <RefreshCw className='size-4' />重置
            </Button>
            <span className='text-xs text-muted-foreground'>校验不会写库 确认导入需要审批角色</span>
          </div>
        </CardContent>
      </Card>

      <Card className='reveal gap-0 overflow-hidden py-0' style={{ animationDelay: '70ms' }}>
        <CardHeader className='border-b bg-muted/30 px-6 py-5'>
          <CardTitle className='flex items-center gap-2 text-base'><ShieldCheck className='size-4 text-sky-600 dark:text-sky-400' />校验与对账</CardTitle>
          <CardDescription>行级错误 金额守恒 写库回读 三道门禁</CardDescription>
        </CardHeader>
        <CardContent className='px-6 py-5'>
          {!summary ? <div className='flex min-h-[320px] flex-col items-center justify-center gap-3 text-center'>
            <span className='flex size-12 items-center justify-center rounded-full bg-muted'><FileSpreadsheet className='size-5 text-muted-foreground' /></span>
            <p className='text-sm font-medium'>还没有校验结果</p>
            <p className='max-w-[260px] text-xs text-muted-foreground'>选择文件后点击校验 这里会显示逐行错误 金额合计与回读结果</p>
          </div> : <>
            <div className={cn('mb-4 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3',
              summary.status === 'blocked' ? 'border-destructive/30 bg-destructive/5' : 'border-emerald-600/20 bg-emerald-600/5')}>
              {StatusIcon && <StatusIcon className={cn('size-4 shrink-0', summary.status === 'blocked' ? 'text-destructive' : 'text-emerald-600 dark:text-emerald-400')} />}
              <div className='min-w-0 flex-1'>
                <p className='text-sm font-medium'>{meta?.label}</p>
                <p className='mt-0.5 truncate text-xs text-muted-foreground'>{summary.filename} · {kindMeta[summary.kind].label}</p>
              </div>
              <Badge variant={meta?.tone}>{summary.rejected_rows ? `${summary.rejected_rows} 行拒绝` : summary.accepted_rows ? `可写入 ${summary.accepted_rows} 行` : '无需写入'}</Badge>
            </div>

            <div className='grid grid-cols-2 gap-2 sm:grid-cols-4'>
              {[
                { label: '总行数', value: summary.total_rows },
                { label: '可写入', value: summary.accepted_rows },
                { label: '已存在跳过', value: summary.skipped_rows },
                { label: '拒绝', value: summary.rejected_rows },
              ].map(item => <div key={item.label} className='rounded-lg border bg-muted/20 px-3 py-2 transition-colors hover:bg-muted/40'>
                <p className='text-[11px] text-muted-foreground'>{item.label}</p>
                <p className='mt-0.5 text-lg font-semibold tabular-nums'>{item.value}</p>
              </div>)}
            </div>

            {!!summary.preview_rows?.length && <div className='mt-4 overflow-x-auto rounded border' aria-label='数据预览'>
              <table className='w-full text-xs'><thead><tr>{Object.keys(summary.preview_rows[0]).map(key => <th className='px-3 py-2 text-left' key={key}>{key}</th>)}</tr></thead>
                <tbody>{summary.preview_rows.map((row, index) => <tr key={index}>{Object.entries(row).map(([key, value]) => <td className='px-3 py-2' key={key}>{value}</td>)}</tr>)}</tbody></table>
            </div>}
            {totals && summary.kind === 'orders' && <dl className='mt-4 grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border px-4 py-3 text-xs'>
              <div className='flex justify-between gap-2'><dt className='text-muted-foreground'>销售额</dt><dd className='font-medium tabular-nums'>{money(totals.sales)}</dd></div>
              <div className='flex justify-between gap-2'><dt className='text-muted-foreground'>净销售额</dt><dd className='font-medium tabular-nums'>{money(totals.net_sales)}</dd></div>
              <div className='flex justify-between gap-2'><dt className='text-muted-foreground'>贡献利润</dt><dd className='font-semibold tabular-nums'>{money(totals.net_profit)}</dd></div>
              <div className='flex justify-between gap-2'><dt className='text-muted-foreground'>采购成本</dt><dd className='font-medium tabular-nums'>{money(totals.cogs)}</dd></div>
            </dl>}
            {totals && summary.kind === 'operations' && <dl className='mt-4 grid grid-cols-3 gap-x-4 rounded-lg border px-4 py-3 text-xs'>
              <div><dt className='text-muted-foreground'>广告支出</dt><dd className='mt-0.5 font-medium tabular-nums'>{money(totals.ad_spend)}</dd></div>
              <div><dt className='text-muted-foreground'>期末库存</dt><dd className='mt-0.5 font-medium tabular-nums'>{totals.inventory.toLocaleString()}</dd></div>
              <div><dt className='text-muted-foreground'>在途库存</dt><dd className='mt-0.5 font-medium tabular-nums'>{totals.inbound_inventory.toLocaleString()}</dd></div>
            </dl>}

            <ul className='mt-4 space-y-1.5'>
              {summary.checks.map(item => <li key={item.code} className='flex items-start gap-2 text-xs'>
                {item.ok ? <CheckCircle2 className='mt-0.5 size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400' /> : <XCircle className='mt-0.5 size-3.5 shrink-0 text-destructive' />}
                <span className='text-muted-foreground'>{item.detail}</span>
              </li>)}
            </ul>

            {summary.issues.length > 0 && <div className='mt-4 max-h-[320px] overflow-auto rounded-lg border'>
              <table className='w-full text-xs'>
                <thead className='bg-muted/50 text-muted-foreground'><tr><th className='px-3 py-2 text-left font-medium'>行</th><th className='px-3 py-2 text-left font-medium'>问题</th><th className='px-3 py-2 text-left font-medium'>说明</th></tr></thead>
                <tbody>{summary.issues.map((item, index) => <tr key={`${item.row}-${item.code}-${index}`} className='border-t transition-colors hover:bg-muted/30'>
                  <td className='px-3 py-2 tabular-nums'>{item.row}</td>
                  <td className='px-3 py-2'><Badge variant='danger'>{issueLabels[item.code] ?? item.code}</Badge></td>
                  <td className='px-3 py-2 text-muted-foreground'>{item.message}</td>
                </tr>)}</tbody>
              </table>
            </div>}

            <div className='mt-5 flex flex-wrap items-center gap-2'>
              <Button onClick={() => void commit()} disabled={!summary.can_commit || !summary.accepted_rows || !isApprover || busy === 'commit'}>
                {busy === 'commit' ? <LoaderCircle className='size-4 animate-spin' /> : <ArrowRight className='size-4' />}
                {summary.accepted_rows ? `确认导入 ${summary.accepted_rows} 行` : '无新增数据'}
              </Button>
              {!isApprover && <span className='text-xs text-muted-foreground'>当前身份只读 切换到审批负责人后可写入</span>}
              {summary.status === 'noop' && <span className='text-xs text-muted-foreground'>文件内容与账本一致 重复导入不会产生第二份记录</span>}
            </div>
          </>}
        </CardContent>
      </Card>
    </div>

    <Card className='reveal gap-0 overflow-hidden py-0' style={{ animationDelay: '140ms' }}>
      <CardHeader className='flex flex-row flex-wrap items-center justify-between gap-3 border-b bg-muted/30 px-6 py-5'>
        <div className='space-y-1'>
          <CardTitle className='text-base'>导入记录</CardTitle>
          <CardDescription>每次提交都保留文件指纹 行数与金额合计</CardDescription>
        </div>
        <Button variant='ghost' size='sm' onClick={() => void loadBatches()} disabled={busy === 'list'}>
          <RefreshCw className={cn('size-4', busy === 'list' && 'animate-spin')} />刷新
        </Button>
      </CardHeader>
      <CardContent className='px-0'>
        {batches.length === 0 ? <div className='px-6 py-12 text-center text-sm text-muted-foreground'>还没有导入批次</div> : <div className='overflow-x-auto'>
          <table className='w-full text-sm'>
            <thead className='bg-muted/30 text-xs text-muted-foreground'><tr>
              <th className='px-6 py-3 text-left font-medium'>时间</th>
              <th className='px-3 py-3 text-left font-medium'>类型</th>
              <th className='px-3 py-3 text-left font-medium'>文件</th>
              <th className='px-3 py-3 text-right font-medium'>写入</th>
              <th className='px-3 py-3 text-right font-medium'>跳过</th>
              <th className='px-3 py-3 text-right font-medium'>合计</th>
              <th className='px-6 py-3 text-left font-medium'>状态</th>
            </tr></thead>
            <tbody>{batches.map(row => <tr key={row.id} className='border-t transition-colors hover:bg-muted/20'>
              <td className='whitespace-nowrap px-6 py-3 text-xs text-muted-foreground'>{row.created_at ? new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }) : '—'}</td>
              <td className='px-3 py-3'><Badge variant='outline'>{row.kind === 'orders' ? '订单' : '经营'}</Badge></td>
              <td className='max-w-[240px] truncate px-3 py-3'>{row.filename}</td>
              <td className='px-3 py-3 text-right tabular-nums'>{row.accepted_rows}</td>
              <td className='px-3 py-3 text-right tabular-nums text-muted-foreground'>{row.skipped_rows}</td>
              <td className='px-3 py-3 text-right tabular-nums'>{row.kind === 'orders' ? money(row.totals.net_profit) : money(row.totals.ad_spend)}</td>
              <td className='px-6 py-3'><Badge variant={row.status === 'committed' ? 'success' : 'info'}>{row.status === 'committed' ? '已写入' : '重复文件'}</Badge></td>
            </tr>)}</tbody>
          </table>
        </div>}
      </CardContent>
    </Card>
  </div>
}

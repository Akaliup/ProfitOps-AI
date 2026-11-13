import type { Analysis, Audit, Demo, Execution, ImportSummary, KnowledgeDocument, KnowledgeDocumentInput, KnowledgeSearch, Metrics, OpsSummary, Recommendation, Scope } from './types'

export class ApiError extends Error {
  constructor(message: string, public code: string, public requestId: string, public status: number, public payload?: unknown) { super(message) }
}

export async function request<T>(path: string, token?: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(path, { ...options, headers }) }
  catch { throw new ApiError('无法连接服务，请确认后端已启动', 'network_error', '', 0) }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(data?.error?.message ?? '服务暂时不可用', data?.error?.code ?? 'api_error', data?.request_id ?? response.headers.get('X-Request-ID') ?? '', response.status, data)
  }
  return data as T
}

async function csvRequest(path: string, token: string, body: string, filename: string, mapping = ''): Promise<ImportSummary> {
  const headers = new Headers({ Authorization: `Bearer ${token}`, 'Content-Type': 'text/csv; charset=utf-8', 'X-Filename': filename })
  let response: Response
  const url = `${path}?filename=${encodeURIComponent(filename)}${mapping ? `&mapping=${encodeURIComponent(mapping)}` : ''}`
  try { response = await fetch(url, { method: 'POST', headers, body }) }
  catch { throw new ApiError('无法连接服务，请确认后端已启动', 'network_error', '', 0) }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(
      data?.error?.message ?? '导入失败', data?.error?.code ?? 'api_error',
      data?.request_id ?? response.headers.get('X-Request-ID') ?? '', response.status, data,
    )
  }
  return data as ImportSummary
}

export function scopeBody(demo: Demo, scope: Scope): Record<string, string> {
  if (!demo.data_range) return {}
  const end = demo.data_range.end_date
  const start = new Date(`${end}T00:00:00Z`)
  start.setUTCDate(start.getUTCDate() - scope.days + 1)
  return { start_date: start.toISOString().slice(0, 10), end_date: end, ...(scope.storeId ? { store_id: scope.storeId } : {}) }
}

export const api = {
  demo: () => request<Demo>('/api/demo'),
  summary: (token: string, demo: Demo, scope: Scope) => request<Metrics>(`/api/dashboard/summary?${new URLSearchParams(scopeBody(demo, scope))}`, token),
  analyses: (token: string) => request<{ items: Analysis[] }>('/api/analyses', token),
  analysis: (token: string, id: string) => request<Analysis>(`/api/analyses/${id}`, token),
  retryAnalysis: (token: string, id: string) => request<Analysis>(`/api/analyses/${id}/retry`, token, { method: 'POST' }),
  recommendations: (token: string) => request<{ items: Recommendation[] }>('/api/recommendations', token),
  audits: (token: string, requestId = '') => request<{ items: Audit[] }>(`/api/audit-logs?limit=100${requestId ? `&request_id=${encodeURIComponent(requestId)}` : ''}`, token),
  ops: (token: string, windowHours = 24) => request<OpsSummary>(`/api/ops/summary?window_hours=${windowHours}`, token),
  start: (token: string, demo: Demo, scope: Scope) => request<Analysis>('/api/analyses', token, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(scopeBody(demo, scope)) }),
  decision: (token: string, id: string, kind: 'approve' | 'reject', reason: string) => request<Recommendation>(`/api/recommendations/${id}/${kind}`, token, { method: 'POST', body: JSON.stringify({ reason }) }),
  execute: (token: string, id: string) => request<Execution>(`/api/recommendations/${id}/execute`, token, { method: 'POST', headers: { 'Idempotency-Key': `web-execute-${id}` } }),
  seed: (token: string) => request<{ seeded: boolean; message: string }>('/api/demo/seed', token, { method: 'POST' }),
  knowledgeDocuments: (token: string) => request<{ items: KnowledgeDocument[] }>('/api/knowledge/documents', token),
  knowledgeSearch: (token: string, query: string) => request<KnowledgeSearch>('/api/knowledge/search', token, { method: 'POST', body: JSON.stringify({ query, top_k: 5 }) }),
  createKnowledgeDocument: (token: string, payload: KnowledgeDocumentInput) => request<KnowledgeDocument>('/api/knowledge/documents', token, { method: 'POST', body: JSON.stringify(payload) }),
  deleteKnowledgeDocument: (token: string, id: string) => request<KnowledgeDocument>(`/api/knowledge/documents/${id}`, token, { method: 'DELETE' }),
  imports: (token: string) => request<{ items: ImportSummary[] }>('/api/imports?limit=50', token),
  previewImport: (token: string, kind: 'orders' | 'operations' | 'ads' | 'inventory', text: string, filename: string, mapping = '') => csvRequest(kind === 'operations' ? `/api/imports/${kind}/preview` : `/api/data/${kind}/preview`, token, text, filename, mapping),
  commitImport: (token: string, kind: 'orders' | 'operations' | 'ads' | 'inventory', text: string, filename: string, mapping = '') => csvRequest(kind === 'operations' ? `/api/imports/${kind}` : `/api/data/${kind}/commit`, token, text, filename, mapping),
  importTemplateUrl: (kind: 'orders' | 'operations' | 'ads' | 'inventory') => `/api/data/template/${kind}`,
}

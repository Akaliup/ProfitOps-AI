export const money = (cents: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(cents / 100)
export const signedMoney = (cents: number) => `${cents > 0 ? '+' : cents < 0 ? '−' : ''}${money(Math.abs(cents))}`
export const percent = (value: number | null | undefined) => value == null ? '无可比基数' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
export const dateTime = (value: string) => new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).toLocaleString('zh-CN', { hour12: false })
export const statusLabels: Record<string, string> = {
  queued: '排队中', running: '分析中', waiting_approval: '等待审批', proposed: '已提出', pending_approval: '待审批',
  approved: '已批准', executing: '模拟执行中', executed: '模拟已执行', rejected: '已拒绝',
  execution_failed: '模拟执行失败', failed: '分析失败', insufficient_data: '数据不足', completed: '已完成',
}
export const llmStatusLabels: Record<string, string> = {
  llm_success: '真实调用成功', deterministic_fallback: '已降级为确定性规则',
  llm_failed: 'LLM 调用失败', not_requested: '未触发 LLM',
}
export const llmReasonLabels: Record<string, string> = {
  not_configured: '未配置 API Key', invalid_evidence_reference: '证据引用无效',
  llm_timeout: '调用超时', llm_auth_failed: '鉴权失败', llm_insufficient_balance: '账户余额不足',
  llm_rate_limited: '触发限流', llm_server_error: '服务端错误', llm_http_error: 'HTTP 错误',
  llm_network_error: '网络错误', llm_invalid_response: '响应不可解析', llm_invalid_json: '输出不是合法 JSON',
  llm_empty_response: '返回空内容', llm_schema_invalid: '输出不符合 Schema',
  no_anomaly: '未检测到异常', insufficient_data: '数据不完整',
}

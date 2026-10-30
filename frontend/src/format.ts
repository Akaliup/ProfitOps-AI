export const money = (cents: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(cents / 100)
export const signedMoney = (cents: number) => `${cents > 0 ? '+' : cents < 0 ? '−' : ''}${money(Math.abs(cents))}`
export const percent = (value: number | null | undefined) => value == null ? '无可比基数' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
export const dateTime = (value: string) => new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).toLocaleString('zh-CN', { hour12: false })
export const statusLabels: Record<string, string> = {
  queued: '排队中', running: '分析中', waiting_approval: '等待审批', proposed: '已提出', pending_approval: '待审批',
  approved: '已批准', executing: '模拟执行中', executed: '模拟已执行', rejected: '已拒绝',
  execution_failed: '模拟执行失败', failed: '分析失败', insufficient_data: '数据不足', completed: '已完成',
}

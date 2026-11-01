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
  llm_tool_round_limit_exceeded: '达到工具循环最大轮数', llm_tool_call_limit_exceeded: '达到最大工具调用次数',
  llm_tool_duplicate_call_id: '重复的 tool_call_id', llm_tool_repeated_call: '重复的工具调用',
  llm_tool_budget_exceeded: '超出工具循环时间预算', llm_tool_scope_mismatch: '工具参数超出分析范围',
  llm_tool_forbidden: '调用了禁止的工具', llm_tool_unknown: '调用了未注册的工具',
  llm_tool_invalid_arguments: '工具参数不合法', llm_tool_execution_failed: '工具执行失败',
  llm_tool_output_truncated: '模型输出被截断', llm_invalid_tool_call: '工具调用格式非法',
  llm_unknown_error: '未知错误',
  no_anomaly: '未检测到异常', insufficient_data: '数据不完整',
}
export const terminationLabels: Record<string, string> = {
  final_answer: '模型给出最终答案', llm_tool_round_limit_exceeded: '达到最大轮数',
  llm_tool_call_limit_exceeded: '达到最大工具调用次数', llm_tool_duplicate_call_id: '重复 tool_call_id',
  llm_tool_repeated_call: '重复工具调用', llm_tool_budget_exceeded: '超出时间预算',
  llm_tool_scope_mismatch: '参数超出分析范围', llm_tool_forbidden: '调用禁止工具',
  llm_tool_unknown: '调用未注册工具', llm_tool_invalid_arguments: '工具参数不合法',
  llm_tool_execution_failed: '工具执行失败', llm_tool_output_truncated: '输出被截断',
  llm_invalid_json: '输出不是合法 JSON', llm_schema_invalid: '输出不符合 Schema',
  llm_timeout: '调用超时', llm_network_error: '网络错误', llm_empty_response: '返回空内容',
  invalid_evidence_reference: '证据引用无效', not_configured: '未配置 API Key',
}

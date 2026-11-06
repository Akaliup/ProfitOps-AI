import { Activity, BookOpen, FileCheck2, Fingerprint, LayoutDashboard, Sparkles, type LucideIcon } from 'lucide-react'

export type Page = 'overview' | 'agent' | 'approvals' | 'knowledge' | 'audit' | 'ops'

export type NavItem = {
  id: Page
  label: string
  icon: LucideIcon
  detail: string
}

export const pages: NavItem[] = [
  { id: 'overview', label: '经营总览', icon: LayoutDashboard, detail: '把增长，变成利润。' },
  { id: 'agent', label: 'Agent 工作台', icon: Sparkles, detail: '从经营信号，到有据可依的行动。' },
  { id: 'approvals', label: '审批与执行', icon: FileCheck2, detail: '每一步行动，都由你掌控。' },
  { id: 'knowledge', label: '运营知识库', icon: BookOpen, detail: '让建议有规则可依，让引用可追溯。' },
  { id: 'ops', label: 'Agent 运维', icon: Activity, detail: '任务、模型与成本的运行视图。' },
  { id: 'audit', label: '审计日志', icon: Fingerprint, detail: '让每一次决策都有迹可循。' },
]

export const navGroups: { title: string; items: Page[] }[] = [
  { title: '工作空间', items: ['overview', 'agent'] },
  { title: '工作流', items: ['approvals', 'knowledge'] },
  { title: '系统', items: ['ops', 'audit'] },
]

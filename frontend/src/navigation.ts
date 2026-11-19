import { Activity, BookOpen, FileCheck2, Package, Search, Settings, Sparkles, LayoutDashboard, Megaphone, type LucideIcon } from 'lucide-react'

export type Page = 'overview' | 'assistants' | 'investigate' | 'inventory' | 'ads' | 'knowledge' | 'approvals' | 'runs' | 'settings'

export type NavItem = {
  id: Page
  label: string
  icon: LucideIcon
  detail: string
  group: '经营' | '专项' | '治理'
}

export const pages: NavItem[] = [
  { id: 'overview', label: '经营总览', icon: LayoutDashboard, detail: '看多店营收、利润与待处理异常', group: '经营' },
  { id: 'assistants', label: '助手中心', icon: Sparkles, detail: '已有任务可运行；未实现的角色保持禁用', group: '经营' },
  { id: 'investigate', label: '经营调查', icon: Search, detail: '利润变化、证据、引用与真实轨迹', group: '经营' },
  { id: 'inventory', label: '库存建议', icon: Package, detail: 'SKU、库存与补货提案', group: '专项' },
  { id: 'ads', label: '广告分析', icon: Megaphone, detail: '账本广告费、活动 ROAS 与预算模拟', group: '专项' },
  { id: 'knowledge', label: '知识与数据', icon: BookOpen, detail: 'CSV 预览、映射、批次与规则检索', group: '专项' },
  { id: 'approvals', label: '审批中心', icon: FileCheck2, detail: '参数差异、证据、毛利红线与模拟执行', group: '治理' },
  { id: 'runs', label: '运行与评测', icon: Activity, detail: '真实运行、错误与可复现评测', group: '治理' },
  { id: 'settings', label: '设置与权限', icon: Settings, detail: '服务端角色与集成的实际状态', group: '治理' },
]

export const navGroups: { title: string; items: Page[] }[] = [
  { title: '经营', items: ['overview', 'assistants', 'investigate'] },
  { title: '专项', items: ['inventory', 'ads', 'knowledge'] },
  { title: '治理', items: ['approvals', 'runs', 'settings'] },
]

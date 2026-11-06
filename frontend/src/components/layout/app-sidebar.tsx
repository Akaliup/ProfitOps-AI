import { BarChart3, ShieldCheck } from 'lucide-react'
import { cn } from '../../lib/utils'
import { navGroups, pages, type Page } from '../../navigation'
import type { Demo, Identity } from '../../types'
import { Badge } from '../ui/badge'

type AppSidebarProps = {
  page: Page
  pending: number
  collapsed: boolean
  mobileOpen: boolean
  demo: Demo | null
  identity: Identity | null
  isApprover: boolean
  onNavigate: (page: Page) => void
  onCloseMobile: () => void
}

export function AppSidebar({ page, pending, collapsed, mobileOpen, demo, identity, isApprover, onNavigate, onCloseMobile }: AppSidebarProps) {
  const byId = new Map(pages.map(item => [item.id, item]))

  return <>
    <div
      className={cn('fixed inset-0 z-40 bg-black/50 backdrop-blur-xs transition-opacity md:hidden', mobileOpen ? 'opacity-100' : 'pointer-events-none opacity-0')}
      onClick={onCloseMobile}
      aria-hidden='true'
    />
    <aside
      data-collapsed={collapsed}
      className={cn(
        'fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r bg-sidebar text-sidebar-foreground transition-[width,transform] duration-200 ease-in-out md:z-30 md:translate-x-0',
        mobileOpen ? 'translate-x-0' : '-translate-x-full',
        collapsed ? 'md:w-14' : 'md:w-64',
      )}
    >
      <div className={cn('flex h-16 shrink-0 items-center gap-2 border-b px-3', collapsed && 'md:justify-center md:px-0')}>
        <div className='flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground'>
          <BarChart3 className='size-4' />
        </div>
        <div className={cn('grid min-w-0 flex-1 leading-tight', collapsed && 'md:hidden')}>
          <span className='truncate text-sm font-semibold'>ProfitOps AI</span>
          <span className='truncate text-xs text-muted-foreground'>跨境增长团队 · 演示</span>
        </div>
      </div>

      <nav aria-label='主导航' className='flex-1 overflow-y-auto px-2 py-3'>
        {navGroups.map(group => <div key={group.title} className='mb-4 last:mb-0'>
          <div className={cn('px-2 pb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground', collapsed && 'md:hidden')}>{group.title}</div>
          <ul className='space-y-0.5'>
            {group.items.map(id => {
              const item = byId.get(id)!
              const active = page === id
              return <li key={id}>
                <button
                  type='button'
                  onClick={() => onNavigate(id)}
                  aria-current={active ? 'page' : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    'flex h-9 w-full items-center gap-2 rounded-md px-2 text-sm text-sidebar-foreground/80 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
                    active && 'bg-sidebar-accent font-medium text-sidebar-accent-foreground',
                    collapsed && 'md:justify-center md:px-0',
                  )}
                >
                  <item.icon className='size-4 shrink-0' />
                  <span className={cn('truncate', collapsed && 'md:hidden')}>{item.label}</span>
                  {id === 'approvals' && pending > 0 && <Badge className={cn('ml-auto h-5 min-w-5 rounded-full px-1 text-[10px] tabular-nums', collapsed && 'md:hidden')}>{pending}</Badge>}
                </button>
              </li>
            })}
          </ul>
        </div>)}
      </nav>

      <div className='shrink-0 border-t p-2'>
        <div className={cn('rounded-lg border bg-muted/40 p-3', collapsed && 'md:hidden')}>
          <div className='flex items-center gap-2 text-xs font-medium'>
            <ShieldCheck className='size-4 text-emerald-600 dark:text-emerald-400' />
            受控模拟环境
          </div>
          <p className='mt-1.5 text-[11px] leading-relaxed text-muted-foreground'>人工审批后执行<br />全程记录，零外部操作</p>
          <div className='mt-2 flex items-center gap-1.5 border-t pt-2 text-[11px] text-muted-foreground'>
            <span className='size-1.5 rounded-full bg-emerald-500' />
            {demo?.llm?.configured ? `DeepSeek ${demo.llm.model}` : '规则引擎在线'}
          </div>
        </div>
        <div className={cn('mt-2 flex items-center gap-2 rounded-md px-2 py-1.5', collapsed && 'md:justify-center md:px-0')}>
          <div className='flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-semibold'>{isApprover ? '审' : '析'}</div>
          <div className={cn('grid min-w-0 flex-1 leading-tight', collapsed && 'md:hidden')}>
            <span className='truncate text-sm font-medium'>{identity?.name ?? '连接中'}</span>
            <span className='truncate text-xs text-muted-foreground'>{isApprover ? '审批负责人' : '经营分析师'} · 本地演示</span>
          </div>
        </div>
      </div>
    </aside>
  </>
}

import { Moon, PanelLeft, ShieldCheck, Sun } from 'lucide-react'
import { cn } from '../../lib/utils'
import type { Demo, Identity } from '../../types'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { NativeSelect } from '../ui/native-select'
import { Separator } from '../ui/separator'

type AppHeaderProps = {
  pageLabel: string
  collapsed: boolean
  demo: Demo | null
  identity: Identity | null
  theme: 'light' | 'dark'
  onToggleCollapsed: () => void
  onOpenMobile: () => void
  onIdentityChange: (identity: Identity) => void
  onToggleTheme: () => void
}

export function AppHeader({ pageLabel, collapsed, demo, identity, theme, onToggleCollapsed, onOpenMobile, onIdentityChange, onToggleTheme }: AppHeaderProps) {
  return (
    <header className='sticky top-0 z-20 flex h-16 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur-md md:px-6'>
      <Button variant='outline' size='icon' className='size-8 md:hidden' onClick={onOpenMobile} aria-label='打开导航'>
        <PanelLeft className='size-4' />
      </Button>
      <Button
        variant='outline'
        size='icon'
        className='hidden size-8 md:inline-flex'
        onClick={onToggleCollapsed}
        aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
      >
        <PanelLeft className='size-4' />
      </Button>
      <Separator orientation='vertical' className='mx-1 hidden h-5 sm:block' />
      <div className='hidden text-sm text-muted-foreground sm:block'>
        工作空间<span className='mx-1.5 text-border'>/</span><span className='font-medium text-foreground'>{pageLabel}</span>
      </div>
      <div className='ml-auto flex items-center gap-2'>
        <Badge variant='outline' className='hidden gap-1.5 font-normal text-muted-foreground lg:inline-flex'>
          <span className='size-1.5 rounded-full bg-emerald-500' />
          演示环境 · 仅模拟
        </Badge>
        <label className='flex items-center gap-1.5 text-muted-foreground'>
          <ShieldCheck className='size-4' />
          <NativeSelect
            aria-label='演示身份'
            className='w-[132px]'
            value={identity?.id ?? ''}
            onChange={event => {
              const next = demo?.identities.find(item => item.id === event.target.value)
              if (next) onIdentityChange(next)
            }}
          >
            {demo?.identities.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </NativeSelect>
        </label>
        <Button
          variant='ghost'
          size='icon'
          className='size-8'
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'}
          title={theme === 'dark' ? '切换浅色模式' : '切换深色模式'}
        >
          {theme === 'dark' ? <Sun className='size-4' /> : <Moon className='size-4' />}
        </Button>
      </div>
    </header>
  )
}

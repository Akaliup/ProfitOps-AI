import * as React from 'react'
import { cn } from '../../lib/utils'

export function Separator({ className, orientation = 'horizontal', ...props }: React.ComponentProps<'div'> & { orientation?: 'horizontal' | 'vertical' }) {
  return (
    <div
      role='separator'
      aria-orientation={orientation}
      data-slot='separator'
      className={cn('shrink-0 bg-border', orientation === 'horizontal' ? 'h-px w-full' : 'h-4 w-px', className)}
      {...props}
    />
  )
}

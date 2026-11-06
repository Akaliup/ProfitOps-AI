import { useState } from 'react'
import { CartesianGrid, LabelList, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Metrics } from '../types'
import { money } from '../format'
import { Button } from './ui/button'
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from './ui/card'
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from './ui/table'

export function TrendChart({ metrics }: { metrics: Metrics }) {
  const [table, setTable] = useState(false)
  const rows = metrics.trend.map(row => ({ ...row, sales_dollars: row.sales / 100, profit_dollars: row.net_profit / 100 }))
  return <Card className='gap-0 overflow-hidden py-0'>
    <CardHeader className='flex flex-row items-center justify-between gap-3 border-b px-5 py-4'>
      <div className='space-y-1'>
        <CardTitle className='text-base'>销售与利润走势</CardTitle>
        <CardDescription className='text-xs'>同一 USD 坐标 · 之前周期与当前周期对照</CardDescription>
      </div>
      <Button variant='ghost' size='sm' onClick={() => setTable(!table)}>{table ? '查看趋势图' : '查看数据表'}</Button>
    </CardHeader>
    {table ? <div className='max-h-[310px] overflow-auto'><Table>
      <TableCaption className='sr-only'>每日销售与净利润</TableCaption>
      <TableHeader><TableRow><TableHead>UTC 业务日</TableHead><TableHead>销售额</TableHead><TableHead>净利润</TableHead><TableHead>广告支出</TableHead></TableRow></TableHeader>
      <TableBody>{rows.map(row => <TableRow key={row.date}><TableCell>{row.date}</TableCell><TableCell>{money(row.sales)}</TableCell><TableCell>{money(row.net_profit)}</TableCell><TableCell>{money(row.ad_spend)}</TableCell></TableRow>)}</TableBody>
    </Table></div> : <div className='h-[300px] w-full px-1 pt-4' role='img' aria-label='每日销售额和净利润趋势，可切换数据表读取全部数值'>
      <ResponsiveContainer width='100%' height='100%' minWidth={0}>
        <LineChart data={rows} margin={{ top: 20, right: 72, left: 0, bottom: 4 }} accessibilityLayer>
          <CartesianGrid vertical={false} stroke='var(--border)' />
          <XAxis dataKey='date' tickFormatter={value => value.slice(5)} tickLine={false} axisLine={false} minTickGap={40} tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
          <YAxis tickFormatter={value => `$${Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(1)}k` : value}`} tickLine={false} axisLine={false} width={62} tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
          <Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)', fontSize: 12 }} labelFormatter={value => `UTC ${value}`} formatter={(value, name) => [`$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2 })}`, name]} cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }} />
          <Legend iconType='plainline' wrapperStyle={{ color: 'var(--foreground)', fontSize: 12, paddingTop: 12 }} />
          <ReferenceLine x={metrics.period.start_date} stroke='var(--border)' label={{ value: '当前周期', position: 'insideTopRight', fill: 'var(--muted-foreground)', fontSize: 11 }} />
          <Line type='monotone' dataKey='sales_dollars' name='销售额' stroke='var(--chart-1)' strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: 'var(--card)', strokeWidth: 2 }} isAnimationActive={false}>
            <LabelList dataKey='sales_dollars' position='right' fill='var(--muted-foreground)' fontSize={10} content={props => props.index === rows.length - 1 ? <text x={Number(props.x) + 9} y={Number(props.y) - 6} fill='var(--muted-foreground)' fontSize={10}>销售额</text> : null} />
          </Line>
          <Line type='monotone' dataKey='profit_dollars' name='净利润' stroke='var(--chart-2)' strokeDasharray='5 3' strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: 'var(--card)', strokeWidth: 2 }} isAnimationActive={false}>
            <LabelList dataKey='profit_dollars' position='right' fill='var(--muted-foreground)' fontSize={10} content={props => props.index === rows.length - 1 ? <text x={Number(props.x) + 9} y={Number(props.y) + 4} fill='var(--muted-foreground)' fontSize={10}>净利润</text> : null} />
          </Line>
        </LineChart>
      </ResponsiveContainer>
    </div>}
    <CardFooter className='justify-between border-t px-5 py-3 text-xs text-muted-foreground'>
      <span>{metrics.previous_period.start_date} — {metrics.period.end_date}</span>
      <span>来源：订单 / 每日经营账本</span>
    </CardFooter>
  </Card>
}

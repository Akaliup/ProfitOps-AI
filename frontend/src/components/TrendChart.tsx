import { useState } from 'react'
import { CartesianGrid, LabelList, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Metrics } from '../types'
import { money } from '../format'

export function TrendChart({ metrics }: { metrics: Metrics }) {
  const [table, setTable] = useState(false)
  const rows = metrics.trend.map(row => ({ ...row, sales_dollars: row.sales / 100, profit_dollars: row.net_profit / 100 }))
  return <section className="panel trend-panel">
    <div className="panel-heading"><div><h2>销售与利润走势</h2><p>同一 USD 坐标 · 之前周期与当前周期对照</p></div><button className="text-button" onClick={() => setTable(!table)}>{table ? '查看趋势图' : '查看数据表'}</button></div>
    {table ? <div className="table-wrap chart-table"><table><caption className="sr-only">每日销售与净利润</caption><thead><tr><th>UTC 业务日</th><th>销售额</th><th>净利润</th><th>广告支出</th></tr></thead><tbody>{rows.map(row => <tr key={row.date}><td>{row.date}</td><td>{money(row.sales)}</td><td>{money(row.net_profit)}</td><td>{money(row.ad_spend)}</td></tr>)}</tbody></table></div> : <div className="chart" role="img" aria-label="每日销售额和净利润趋势，可切换数据表读取全部数值">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={rows} margin={{ top: 20, right: 72, left: 0, bottom: 4 }} accessibilityLayer>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="date" tickFormatter={value => value.slice(5)} tickLine={false} axisLine={false} minTickGap={40} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
          <YAxis tickFormatter={value => `$${Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(1)}k` : value}`} tickLine={false} axisLine={false} width={62} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
          <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, color: 'var(--ink)' }} labelFormatter={value => `UTC ${value}`} formatter={(value, name) => [`$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2 })}`, name]} cursor={{ stroke: 'var(--muted)', strokeWidth: 1 }} />
          <Legend iconType="plainline" wrapperStyle={{ color: 'var(--ink)', fontSize: 12, paddingTop: 12 }} />
          <ReferenceLine x={metrics.period.start_date} stroke="var(--border-strong)" label={{ value: '当前周期', position: 'insideTopRight', fill: 'var(--muted)', fontSize: 11 }} />
          <Line type="monotone" dataKey="sales_dollars" name="销售额" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: 'var(--surface)', strokeWidth: 2 }} isAnimationActive={false}>
            <LabelList dataKey="sales_dollars" position="right" fill="var(--secondary)" fontSize={10} content={props => props.index === rows.length - 1 ? <text x={Number(props.x) + 9} y={Number(props.y) - 6} fill="var(--secondary)" fontSize={10}>销售额</text> : null} />
          </Line>
          <Line type="monotone" dataKey="profit_dollars" name="净利润" stroke="var(--series-2)" strokeDasharray="5 3" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: 'var(--surface)', strokeWidth: 2 }} isAnimationActive={false}>
            <LabelList dataKey="profit_dollars" position="right" fill="var(--secondary)" fontSize={10} content={props => props.index === rows.length - 1 ? <text x={Number(props.x) + 9} y={Number(props.y) + 4} fill="var(--secondary)" fontSize={10}>净利润</text> : null} />
          </Line>
        </LineChart>
      </ResponsiveContainer>
    </div>}
    <div className="chart-foot">{metrics.previous_period.start_date} — {metrics.period.end_date}<span>来源：订单 / 每日经营账本</span></div>
  </section>
}

'use client'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import { formatMonthShort } from '@/lib/utils'

interface DataPoint {
  month: string
  income: number
  expense: number
  diff: number
}

interface Props {
  data: DataPoint[]
}

function formatYenShort(v: number) {
  if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(0)}万`
  return `${v.toLocaleString()}`
}

export default function IncomeExpenseTrend({ data }: Props) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
        <XAxis dataKey="month" tickFormatter={formatMonthShort} tick={{ fontSize: 11, fill: '#64748b' }} />
        <YAxis tickFormatter={formatYenShort} tick={{ fontSize: 11, fill: '#64748b' }} width={40} />
        <Tooltip
          formatter={(v, name) => [
            `${Number(v).toLocaleString()}円`,
            name === 'income' ? '収入' : name === 'expense' ? '支出' : '収支差'
          ]}
          labelFormatter={(l) => formatMonthShort(String(l))}
        />
        <Legend formatter={v => v === 'income' ? '収入' : v === 'expense' ? '支出' : '収支差'} />
        <Bar dataKey="income" fill="#16a34a" opacity={0.85} radius={[3, 3, 0, 0]} />
        <Bar dataKey="expense" fill="#dc2626" opacity={0.85} radius={[3, 3, 0, 0]} />
        <Line type="monotone" dataKey="diff" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

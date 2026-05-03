'use client'
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from 'recharts'

const COLORS = ['#dc2626', '#7c3aed', '#d97706', '#3b82f6', '#16a34a', '#6366f1', '#0891b2', '#be185d']

interface DataPoint {
  name: string
  value: number
}

interface Props {
  data: DataPoint[]
}

export default function CategoryPieChart({ data }: Props) {
  if (data.length === 0) {
    return <p className="text-center text-sm py-8" style={{ color: 'var(--kb-muted)' }}>データがありません</p>
  }

  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={60}
          outerRadius={100}
          paddingAngle={2}
          dataKey="value"
        >
          {data.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip formatter={(v) => [`${Number(v).toLocaleString()}円`]} />
        <Legend formatter={v => v} wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

import { normalizeMonth, currentMonth, formatYen, formatMonth, monthToInputValue } from '@/lib/utils'
import { getMonthlyIncomes, getVariableExpenses, getFixedTemplates } from '@/lib/kakeibo/queries'
import { calcIncomeTotal, calcFixedTotal } from '@/lib/kakeibo/calculations'
import { OWNER_LABELS, VAR_TYPE_LABELS } from '@/lib/kakeibo/types'
import Link from 'next/link'

interface PageProps {
  searchParams: Promise<{ month?: string }>
}

export default async function AnalysisPage({ searchParams }: PageProps) {
  const params = await searchParams
  const month = normalizeMonth(params.month ?? currentMonth().substring(0, 7)) || currentMonth()

  const [incomes, variables, fixed] = await Promise.all([
    getMonthlyIncomes(month),
    getVariableExpenses(month),
    getFixedTemplates(true),
  ])

  const incomeTotal = calcIncomeTotal(incomes)
  const fixedTotal = calcFixedTotal(fixed)

  // Separate variable expenses by type
  const cards = variables.filter(e => e.var_type === 'CARD')
  const advances = variables.filter(e => e.var_type === 'ADVANCE')
  const pensions = variables.filter(e => e.var_type === 'PENSION')
  const others = variables.filter(e => e.var_type === 'OTHER')

  const cardTotal = cards.reduce((s, e) => s + e.amount, 0)
  const advanceTotal = advances.reduce((s, e) => s + e.amount, 0)
  const pensionTotal = pensions.reduce((s, e) => s + e.amount, 0)
  const otherTotal = others.reduce((s, e) => s + e.amount, 0)

  const expenseTotal = fixedTotal + cardTotal + pensionTotal + otherTotal
  const diff = incomeTotal - expenseTotal

  const sections = [
    {
      key: 'income',
      title: '収入',
      total: incomeTotal,
      items: incomes,
      color: 'var(--kb-income)',
      bg: 'var(--kb-income-bg)',
      border: '#86efac',
      getLabel: (i: typeof incomes[0]) => `${OWNER_LABELS[i.owner]} / ${i.category?.name ?? ''}${i.memo ? ` (${i.memo})` : ''}`,
    },
    {
      key: 'fixed',
      title: '固定費',
      total: fixedTotal,
      items: fixed.map(t => ({ ...t, amount: t.amount, owner: t.owner })),
      color: 'var(--kb-fixed)',
      bg: 'var(--kb-fixed-bg)',
      border: '#c4b5fd',
      getLabel: (t: typeof fixed[0]) => `${OWNER_LABELS[t.owner]} / ${t.category?.name ?? ''}${t.memo ? ` (${t.memo})` : ''}`,
    },
    {
      key: 'advance',
      title: '立替',
      total: advanceTotal,
      items: advances,
      color: 'var(--kb-advance)',
      bg: 'var(--kb-advance-bg)',
      border: '#fcd34d',
      getLabel: (e: typeof variables[0]) => `${OWNER_LABELS[e.owner]} / ${e.card?.name ?? e.category?.name ?? ''}${e.memo ? ` (${e.memo})` : ''}`,
    },
    {
      key: 'card',
      title: 'カード支払い',
      total: cardTotal,
      items: cards,
      color: 'var(--kb-expense)',
      bg: 'var(--kb-expense-bg)',
      border: '#fca5a5',
      getLabel: (e: typeof variables[0]) => `${OWNER_LABELS[e.owner]} / ${e.card?.name ?? ''}${e.memo ? ` (${e.memo})` : ''}`,
    },
  ] as const

  return (
    <div className="p-4 space-y-5">
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold" style={{ color: 'var(--kb-text)' }}>収支分析</h1>
        <form method="get" className="flex gap-2">
          <input type="month" name="month" defaultValue={monthToInputValue(month)}
            className="border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
          <button type="submit" className="bg-blue-600 text-white text-sm px-3 py-1.5 rounded-lg">表示</button>
        </form>
      </div>

      {/* Summary row */}
      <div className="card p-4 space-y-3">
        <p className="text-sm font-semibold" style={{ color: 'var(--kb-muted)' }}>{formatMonth(month)} サマリー</p>
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-xl p-3" style={{ background: 'var(--kb-income-bg)' }}>
            <p className="text-xs" style={{ color: 'var(--kb-muted)' }}>収入合計</p>
            <p className="text-lg font-bold" style={{ color: 'var(--kb-income)' }}>{formatYen(incomeTotal)}</p>
          </div>
          <div className="rounded-xl p-3" style={{ background: 'var(--kb-expense-bg)' }}>
            <p className="text-xs" style={{ color: 'var(--kb-muted)' }}>支出合計</p>
            <p className="text-lg font-bold" style={{ color: 'var(--kb-expense)' }}>{formatYen(expenseTotal)}</p>
          </div>
          <div className="rounded-xl p-3" style={{ background: diff >= 0 ? 'var(--kb-income-bg)' : 'var(--kb-expense-bg)' }}>
            <p className="text-xs" style={{ color: 'var(--kb-muted)' }}>収支差</p>
            <p className="text-lg font-bold" style={{ color: diff >= 0 ? 'var(--kb-income)' : 'var(--kb-expense)' }}>
              {diff >= 0 ? '+' : ''}{formatYen(diff)}
            </p>
          </div>
          <div className="rounded-xl p-3" style={{ background: 'var(--kb-advance-bg)' }}>
            <p className="text-xs" style={{ color: 'var(--kb-muted)' }}>立替合計</p>
            <p className="text-lg font-bold" style={{ color: 'var(--kb-advance)' }}>{formatYen(advanceTotal)}</p>
          </div>
        </div>
      </div>

      {/* Per-type bar chart (CSS only) */}
      {(() => {
        const max = Math.max(incomeTotal, fixedTotal, cardTotal, advanceTotal, 1)
        const bars = [
          { label: '収入', value: incomeTotal, color: 'var(--kb-income)' },
          { label: '固定費', value: fixedTotal, color: 'var(--kb-fixed)' },
          { label: 'カード', value: cardTotal, color: 'var(--kb-expense)' },
          { label: '立替', value: advanceTotal, color: 'var(--kb-advance)' },
          { label: '年金保険', value: pensionTotal, color: '#6366f1' },
          { label: 'その他', value: otherTotal, color: 'var(--kb-muted)' },
        ]
        return (
          <div className="card p-4 space-y-3">
            <p className="section-title">内訳バー</p>
            {bars.map(b => (
              <div key={b.label} className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span style={{ color: 'var(--kb-text)' }}>{b.label}</span>
                  <span style={{ color: b.color }} className="font-semibold">{formatYen(b.value)}</span>
                </div>
                <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--kb-surface2)' }}>
                  <div className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${(b.value / max) * 100}%`, background: b.color }} />
                </div>
              </div>
            ))}
          </div>
        )
      })()}

      {/* Sections */}
      {sections.map(section => (
        <div key={section.key}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full" style={{ background: section.color }} />
              <p className="section-title mb-0">{section.title}</p>
            </div>
            <span className="text-sm font-bold" style={{ color: section.color }}>{formatYen(section.total)}</span>
          </div>
          <div className="card divide-y" style={{ borderColor: section.border }}>
            {section.items.length === 0 && (
              <p className="text-sm text-center py-4" style={{ color: 'var(--kb-muted)' }}>データなし</p>
            )}
            {section.items.map((item) => (
              <div key={item.id} className="flex items-center justify-between px-4 py-3">
                <p className="text-sm flex-1 truncate" style={{ color: 'var(--kb-text)' }}>
                  {(section as typeof sections[0]).getLabel(item as never)}
                </p>
                <span className="text-sm font-semibold ml-2" style={{ color: section.color }}>
                  {formatYen(item.amount)}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

import { getLast12Months, formatYen } from '@/lib/utils'
import { getMonthlyIncomesRange, getVariableExpensesRange, getFixedTemplates } from '@/lib/kakeibo/queries'
import { calcIncomeTotal, calcFixedTotal, calcVariableTotal, calcExpenseTotal, calcDiff } from '@/lib/kakeibo/calculations'
import IncomeExpenseTrend from '@/components/charts/IncomeExpenseTrend'
import CategoryPieChart from '@/components/charts/CategoryPieChart'

export default async function ChartsPage() {
  const months = getLast12Months()
  const from = months[0]
  const to = months[months.length - 1]

  const [allIncomes, allVariables, fixedTemplates] = await Promise.all([
    getMonthlyIncomesRange(from, to),
    getVariableExpensesRange(from, to),
    getFixedTemplates(true),
  ])

  // Build monthly trend data
  const trendData = months.map(month => {
    const incomes = allIncomes.filter(i => i.month === month)
    const variables = allVariables.filter(v => v.month === month)
    const income = calcIncomeTotal(incomes)
    const expense = calcExpenseTotal(fixedTemplates, variables)
    return {
      month,
      income,
      expense,
      diff: calcDiff(income, expense),
    }
  })

  // Category breakdown for latest month
  const latestMonth = to
  const latestVariables = allVariables.filter(v => v.month === latestMonth)
  const categoryMap = new Map<string, number>()

  // Fixed expenses
  fixedTemplates.forEach(t => {
    const name = t.category?.name ?? '固定費'
    categoryMap.set(name, (categoryMap.get(name) ?? 0) + t.amount)
  })

  // Variable expenses
  latestVariables.forEach(e => {
    const name = e.category?.name ?? e.var_type
    categoryMap.set(name, (categoryMap.get(name) ?? 0) + e.amount)
  })

  const pieData = Array.from(categoryMap.entries())
    .map(([name, value]) => ({ name, value }))
    .filter(d => d.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)

  const totalIncome = trendData.reduce((s, d) => s + d.income, 0)
  const totalExpense = trendData.reduce((s, d) => s + d.expense, 0)
  const avgIncome = Math.round(totalIncome / months.length)
  const avgExpense = Math.round(totalExpense / months.length)

  return (
    <div className="p-4 space-y-5">
      <h1 className="text-lg font-bold pt-2" style={{ color: 'var(--kb-text)' }}>チャート</h1>

      {/* 12-month summary */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card p-3 text-center">
          <p className="text-xs" style={{ color: 'var(--kb-muted)' }}>月平均収入</p>
          <p className="text-base font-bold" style={{ color: 'var(--kb-income)' }}>{formatYen(avgIncome)}</p>
        </div>
        <div className="card p-3 text-center">
          <p className="text-xs" style={{ color: 'var(--kb-muted)' }}>月平均支出</p>
          <p className="text-base font-bold" style={{ color: 'var(--kb-expense)' }}>{formatYen(avgExpense)}</p>
        </div>
      </div>

      {/* Trend chart */}
      <div className="card p-4">
        <p className="section-title">12ヶ月 収入 vs 支出</p>
        <IncomeExpenseTrend data={trendData} />
      </div>

      {/* Category pie */}
      <div className="card p-4">
        <p className="section-title">直近月 支出カテゴリ内訳</p>
        <CategoryPieChart data={pieData} />
      </div>
    </div>
  )
}

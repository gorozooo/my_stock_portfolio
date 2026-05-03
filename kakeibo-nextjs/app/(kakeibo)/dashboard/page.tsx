import { Suspense } from 'react'
import { formatYen, formatMonth, currentMonth, normalizeMonth, diffLabel } from '@/lib/utils'
import { OWNER_LABELS } from '@/lib/kakeibo/types'
import {
  getMonthlyIncomes, getVariableExpenses, getFixedTemplates,
  getBankBalances, getSnapshot, getAllSnapshots, getTodos, getMemo,
  getAccounts, getAppConfig,
} from '@/lib/kakeibo/queries'
import {
  calcIncomeTotal, calcFixedTotal, calcVariableTotal, calcExpenseTotal,
  calcDiff, calcHouseSavings, groupBalancesByOwner,
} from '@/lib/kakeibo/calculations'
import TodoSection from '@/components/dashboard/TodoSection'
import DashboardMemo from '@/components/dashboard/DashboardMemo'
import SnapshotButton from '@/components/dashboard/SnapshotButton'
import MonthSelector from '@/components/layout/MonthSelector'
import { logoutAction } from '@/actions/auth'
import { TrendingUp, TrendingDown, Minus, LogOut } from 'lucide-react'

interface PageProps {
  searchParams: Promise<{ month?: string }>
}

export default async function DashboardPage({ searchParams }: PageProps) {
  const params = await searchParams
  const monthParam = params.month
  const month = normalizeMonth(monthParam ?? currentMonth().substring(0, 7)) || currentMonth()

  const [incomes, variables, fixed, accounts, allSnapshots] = await Promise.all([
    getMonthlyIncomes(month),
    getVariableExpenses(month),
    getFixedTemplates(true),
    getAccounts(),
    getAllSnapshots(),
  ])

  const [balances, snapshot, todos, memo, baseMonth, baseValueStr] = await Promise.all([
    getBankBalances(month),
    getSnapshot(month),
    getTodos(month),
    getMemo(month),
    getAppConfig('house_savings_base_month'),
    getAppConfig('house_savings_base_value'),
  ])

  const income = calcIncomeTotal(incomes)
  const fixedTotal = calcFixedTotal(fixed)
  const variable = calcVariableTotal(variables)
  const expenseTotal = calcExpenseTotal(fixed, variables)
  const diff = calcDiff(income, expenseTotal)

  const baseValue = Number(baseValueStr ?? '0')
  const houseSavings = calcHouseSavings(month, allSnapshots, baseMonth ?? '2025-12-01', baseValue, diff)

  const balancesByOwner = groupBalancesByOwner(accounts, balances, month)

  const diffPositive = diff >= 0
  const DiffIcon = diff > 0 ? TrendingUp : diff < 0 ? TrendingDown : Minus

  return (
    <div className="p-4 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-[var(--kb-text)]">家計簿</h1>
        <div className="flex items-center gap-2">
          <MonthSelector month={month} />
          <form action={logoutAction}>
            <button type="submit" className="p-1.5 text-[var(--kb-muted)] hover:text-red-500 transition-colors">
              <LogOut size={18} />
            </button>
          </form>
        </div>
      </div>

      {/* Monthly Summary Card */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-semibold text-[var(--kb-muted)]">{formatMonth(month)}</span>
          <SnapshotButton month={month} hasSnapshot={!!snapshot} />
        </div>
        <div className="grid grid-cols-3 gap-3 mb-4">
          <div className="text-center">
            <p className="text-xs text-[var(--kb-muted)] mb-1">収入</p>
            <p className="font-bold text-[var(--kb-income)] text-sm">{formatYen(income)}</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-[var(--kb-muted)] mb-1">支出</p>
            <p className="font-bold text-[var(--kb-expense)] text-sm">{formatYen(expenseTotal)}</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-[var(--kb-muted)] mb-1">収支</p>
            <p className={`font-bold text-sm flex items-center justify-center gap-0.5 ${diffPositive ? 'text-[var(--kb-income)]' : 'text-[var(--kb-expense)]'}`}>
              <DiffIcon size={14} />
              {diffLabel(diff)}
            </p>
          </div>
        </div>
        {/* Breakdown */}
        <div className="space-y-1.5 pt-3 border-t">
          <div className="flex justify-between text-sm">
            <span className="text-[var(--kb-muted)]">固定費</span>
            <span className="font-medium text-[var(--kb-fixed)]">{formatYen(fixedTotal)}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-[var(--kb-muted)]">変動費</span>
            <span className="font-medium text-[var(--kb-expense)]">{formatYen(variable)}</span>
          </div>
        </div>
      </div>

      {/* KPI: House Savings */}
      <div className="card p-4">
        <p className="text-xs text-[var(--kb-muted)] mb-1">家の貯蓄</p>
        <p className={`text-2xl font-bold ${houseSavings >= 0 ? 'text-[var(--kb-income)]' : 'text-[var(--kb-expense)]'}`}>
          {formatYen(houseSavings)}
        </p>
      </div>

      {/* Bank Balances */}
      {(['HOUSE', 'B', 'G'] as const).map(owner => {
        const ownerBalances = balancesByOwner[owner]
        if (ownerBalances.length === 0) return null
        const total = ownerBalances.reduce((s, b) => s + b.balance, 0)
        return (
          <div key={owner}>
            <p className="section-title">{OWNER_LABELS[owner]} の口座</p>
            <div className="card divide-y">
              {ownerBalances.map(({ account, balance }) => (
                <div key={account.id} className="flex justify-between items-center px-4 py-3">
                  <span className="text-sm text-[var(--kb-text)]">{account.name}</span>
                  <span className="text-sm font-semibold text-[var(--kb-bank)]">{formatYen(balance)}</span>
                </div>
              ))}
              <div className="flex justify-between items-center px-4 py-3 bg-[var(--kb-surface2)]">
                <span className="text-xs font-semibold text-[var(--kb-muted)]">合計</span>
                <span className="text-sm font-bold text-[var(--kb-bank)]">{formatYen(total)}</span>
              </div>
            </div>
          </div>
        )
      })}

      {/* Todos */}
      <TodoSection todos={todos} month={month} />

      {/* Memo */}
      <DashboardMemo month={month} initialMemo={memo} />

      <div className="h-4" />
    </div>
  )
}

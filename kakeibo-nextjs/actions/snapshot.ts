'use server'
import { revalidatePath } from 'next/cache'
import { redirect } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import { normalizeMonth } from '@/lib/utils'
import {
  calcIncomeTotal, calcFixedTotal, calcVariableTotal, calcExpenseTotal, calcDiff,
  getLatestBalance, calcHouseSavings
} from '@/lib/kakeibo/calculations'
import { getMonthlyIncomes, getVariableExpenses, getFixedTemplates, getBankBalances, getAllSnapshots, getAppConfig } from '@/lib/kakeibo/queries'

export async function confirmSnapshotAction(month: string) {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  const m = normalizeMonth(month)
  const [incomes, variables, fixed, balances, allSnapshots] = await Promise.all([
    getMonthlyIncomes(m),
    getVariableExpenses(m),
    getFixedTemplates(true),
    getBankBalances(m),
    getAllSnapshots(),
  ])

  const income = calcIncomeTotal(incomes)
  const fixedTotal = calcFixedTotal(fixed)
  const variable = calcVariableTotal(variables)
  const expense_total = calcExpenseTotal(fixed, variables)
  const diff = calcDiff(income, expense_total)

  // KPI calculations (simplified — extend when portfolio is integrated)
  const baseMonth = (await getAppConfig('house_savings_base_month')) ?? '2025-12-01'
  const baseValue = Number((await getAppConfig('house_savings_base_value')) ?? '-623573')
  const kpi_house_savings = calcHouseSavings(m, allSnapshots, baseMonth, baseValue, diff)

  const { error } = await supabase.from('monthly_snapshots').upsert({
    month: m,
    income,
    fixed: fixedTotal,
    variable,
    expense_total,
    diff,
    kpi_house_savings,
    kpi_total_assets: 0,
    kpi_invest_total: 0,
    rakuten_eval: 0,
    rakuten_cash_free: 0,
    rakuten_bank_b: 0,
    aeon_bank_house: 0,
    locked_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }, { onConflict: 'month' })

  if (error) return { error: error.message }
  revalidatePath('/dashboard')
  return { success: true }
}

import { createClient } from '@/lib/supabase/server'
import type {
  Account, Category, MonthlyIncome, MonthlyVariableExpense,
  FixedExpenseTemplate, BankBalance, MonthlySnapshot,
  MonthlyTodo, MonthlyDashboardMemo,
} from './types'

export async function getAccounts(kind?: 'ACCOUNT' | 'CARD'): Promise<Account[]> {
  const supabase = await createClient()
  let query = supabase.from('accounts').select('*').order('owner').order('id')
  if (kind) query = query.eq('kind', kind)
  const { data } = await query
  return (data as Account[]) ?? []
}

export async function getCategories(type?: 'INCOME' | 'EXPENSE'): Promise<Category[]> {
  const supabase = await createClient()
  let query = supabase.from('categories').select('*').order('sort_order').order('id')
  if (type) query = query.eq('type', type)
  const { data } = await query
  return (data as Category[]) ?? []
}

export async function getMonthlyIncomes(month: string): Promise<MonthlyIncome[]> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_incomes')
    .select('*, category:categories(*)')
    .eq('month', month)
    .order('id')
  return (data as MonthlyIncome[]) ?? []
}

export async function getMonthlyIncomesRange(from: string, to: string): Promise<MonthlyIncome[]> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_incomes')
    .select('*, category:categories(*)')
    .gte('month', from)
    .lte('month', to)
    .order('month')
  return (data as MonthlyIncome[]) ?? []
}

export async function getVariableExpenses(month: string): Promise<MonthlyVariableExpense[]> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_variable_expenses')
    .select('*, category:categories(*), card:accounts(*)')
    .eq('month', month)
    .order('id')
  return (data as MonthlyVariableExpense[]) ?? []
}

export async function getVariableExpensesRange(from: string, to: string): Promise<MonthlyVariableExpense[]> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_variable_expenses')
    .select('*, category:categories(*), card:accounts(*)')
    .gte('month', from)
    .lte('month', to)
    .order('month')
  return (data as MonthlyVariableExpense[]) ?? []
}

export async function getFixedTemplates(activeOnly = false): Promise<FixedExpenseTemplate[]> {
  const supabase = await createClient()
  let query = supabase
    .from('fixed_expense_templates')
    .select('*, category:categories(*)')
    .order('owner').order('id')
  if (activeOnly) query = query.eq('is_active', true)
  const { data } = await query
  return (data as FixedExpenseTemplate[]) ?? []
}

export async function getBankBalances(month?: string): Promise<BankBalance[]> {
  const supabase = await createClient()
  let query = supabase
    .from('bank_balances')
    .select('*, account:accounts(*)')
    .order('month', { ascending: false })
    .order('account_id')
  if (month) query = query.lte('month', month)
  const { data } = await query
  return (data as BankBalance[]) ?? []
}

export async function getSnapshot(month: string): Promise<MonthlySnapshot | null> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_snapshots')
    .select('*')
    .eq('month', month)
    .single()
  return (data as MonthlySnapshot) ?? null
}

export async function getAllSnapshots(): Promise<MonthlySnapshot[]> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_snapshots')
    .select('*')
    .order('month')
  return (data as MonthlySnapshot[]) ?? []
}

export async function getTodos(month: string): Promise<MonthlyTodo[]> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_todos')
    .select('*')
    .eq('month', month)
    .order('sort_order')
    .order('id')
  return (data as MonthlyTodo[]) ?? []
}

export async function getMemo(month: string): Promise<string> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('monthly_dashboard_memos')
    .select('memo')
    .eq('month', month)
    .single()
  return (data as MonthlyDashboardMemo)?.memo ?? ''
}

export async function getAppConfig(key: string): Promise<string | null> {
  const supabase = await createClient()
  const { data } = await supabase
    .from('app_config')
    .select('value')
    .eq('key', key)
    .single()
  return (data as { value: string })?.value ?? null
}

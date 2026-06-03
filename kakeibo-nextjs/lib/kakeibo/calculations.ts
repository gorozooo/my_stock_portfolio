import type {
  MonthlyIncome,
  MonthlyVariableExpense,
  FixedExpenseTemplate,
  BankBalance,
  MonthlySnapshot,
  Account,
  OwnerType,
} from './types'

// House savings cumulative calculation from a base value
export function calcHouseSavings(
  targetMonth: string,
  snapshots: MonthlySnapshot[],
  baseMonth: string,
  baseValue: number,
  dynamicDiff: number // current month diff if not snapshotted
): number {
  const sorted = [...snapshots].sort((a, b) => a.month.localeCompare(b.month))
  let savings = baseValue
  for (const snap of sorted) {
    if (snap.month > baseMonth && snap.month <= targetMonth) {
      savings += snap.diff
    }
  }
  // If current month has no snapshot, add dynamic diff
  const hasSnapshot = snapshots.some(s => s.month === targetMonth)
  if (!hasSnapshot) {
    savings += dynamicDiff
  }
  return savings
}

// Income total for a month
export function calcIncomeTotal(incomes: MonthlyIncome[]): number {
  return incomes.reduce((sum, i) => sum + i.amount, 0)
}

// Fixed expense total (active templates only)
export function calcFixedTotal(templates: FixedExpenseTemplate[]): number {
  return templates.filter(t => t.is_active).reduce((sum, t) => sum + t.amount, 0)
}

// Variable expense total (excluding B/G CARD type — only HOUSE CARD + all non-CARD)
export function calcVariableTotal(expenses: MonthlyVariableExpense[]): number {
  return expenses
    .filter(e => !(e.owner !== 'HOUSE' && e.var_type === 'CARD'))
    .reduce((sum, e) => sum + e.amount, 0)
}

// Advance (立替) total — var_type = ADVANCE only
export function calcAdvanceTotal(expenses: MonthlyVariableExpense[], owner?: OwnerType): number {
  return expenses
    .filter(e => e.var_type === 'ADVANCE' && (owner ? e.owner === owner : true))
    .reduce((sum, e) => sum + e.amount, 0)
}

// Card bill total for a specific owner
export function calcCardBillTotal(expenses: MonthlyVariableExpense[], owner: OwnerType): number {
  return expenses
    .filter(e => e.var_type === 'CARD' && e.owner === owner)
    .reduce((sum, e) => sum + e.amount, 0)
}

// Allowance needed for a person = fixed allowance + advances - card bills
export function calcOkodukai(
  owner: OwnerType,
  fixedTemplates: FixedExpenseTemplate[],
  variableExpenses: MonthlyVariableExpense[],
  allowanceCategoryKeyword: string = 'お小遣い'
): number {
  const fixedAllowance = fixedTemplates
    .filter(t => t.is_active && t.owner === owner &&
      t.category?.name?.includes(allowanceCategoryKeyword))
    .reduce((sum, t) => sum + t.amount, 0)
  const advances = calcAdvanceTotal(variableExpenses, owner)
  const cardBills = calcCardBillTotal(variableExpenses, owner)
  return fixedAllowance + advances - cardBills
}

// Expense total = fixed + variable (as defined above)
export function calcExpenseTotal(
  templates: FixedExpenseTemplate[],
  expenses: MonthlyVariableExpense[]
): number {
  return calcFixedTotal(templates) + calcVariableTotal(expenses)
}

// Monthly diff = income - expense
export function calcDiff(income: number, expense: number): number {
  return income - expense
}

// Bank balance lookup with fallback to most recent
export function getLatestBalance(
  balances: { month: string; balance: number }[],
  targetMonth: string
): number {
  const eligible = balances
    .filter(b => b.month <= targetMonth)
    .sort((a, b) => b.month.localeCompare(a.month))
  return eligible[0]?.balance ?? 0
}

// Group bank balances by owner
export function groupBalancesByOwner(
  accounts: Account[],
  balances: BankBalance[],
  targetMonth: string
): Record<OwnerType, { account: Account; balance: number }[]> {
  const result: Record<OwnerType, { account: Account; balance: number }[]> = {
    HOUSE: [],
    B: [],
    G: [],
  }
  for (const account of accounts) {
    if (account.kind !== 'ACCOUNT') continue
    const accountBalances = balances
      .filter(b => b.account_id === account.id)
      .map(b => ({ month: b.month, balance: b.balance }))
    const balance = getLatestBalance(accountBalances, targetMonth)
    result[account.owner].push({ account, balance })
  }
  return result
}

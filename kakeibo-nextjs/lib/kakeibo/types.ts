export type OwnerType = 'HOUSE' | 'B' | 'G'
export type AccountKind = 'ACCOUNT' | 'CARD'
export type CategoryType = 'INCOME' | 'EXPENSE'
export type VarType = 'CARD' | 'ADVANCE' | 'PENSION' | 'OTHER'
export type TodoStatus = 'TODO' | 'DOING' | 'DONE'

export const OWNER_LABELS: Record<OwnerType, string> = {
  HOUSE: '家',
  B: 'ぼーや',
  G: 'ごろ',
}
export const OWNERS: OwnerType[] = ['HOUSE', 'B', 'G']

export const VAR_TYPE_LABELS: Record<VarType, string> = {
  CARD: 'カード',
  ADVANCE: '立替',
  PENSION: '年金/保険',
  OTHER: 'その他',
}

export interface Account {
  id: number
  kind: AccountKind
  owner: OwnerType
  name: string
  created_at: string
}

export interface Category {
  id: number
  type: CategoryType
  code: string
  name: string
  sort_order: number
  created_at: string
}

export interface MonthlyIncome {
  id: number
  month: string
  owner: OwnerType
  category_id: number
  amount: number
  memo: string
  created_at: string
  category?: Category
}

export interface MonthlyVariableExpense {
  id: number
  month: string
  owner: OwnerType
  var_type: VarType
  category_id: number
  card_id: number | null
  amount: number
  memo: string
  created_at: string
  category?: Category
  card?: Account
}

export interface FixedExpenseTemplate {
  id: number
  owner: OwnerType
  category_id: number
  amount: number
  memo: string
  is_active: boolean
  created_at: string
  category?: Category
}

export interface BankBalance {
  id: number
  month: string
  account_id: number
  balance: number
  updated_at: string
  account?: Account
}

export interface MonthlySnapshot {
  id: number
  month: string
  income: number
  fixed: number
  variable: number
  expense_total: number
  diff: number
  kpi_total_assets: number
  kpi_house_savings: number
  kpi_invest_total: number
  rakuten_eval: number
  rakuten_cash_free: number
  rakuten_bank_b: number
  aeon_bank_house: number
  locked_at: string
  updated_at: string
}

export interface MonthlyTodo {
  id: number
  month: string
  title: string
  note: string
  status: TodoStatus
  sort_order: number
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface MonthlyDashboardMemo {
  id: number
  month: string
  memo: string
  created_at: string
  updated_at: string
}

export interface DashboardData {
  month: string
  income: number
  fixed: number
  variable: number
  expenseTotal: number
  diff: number
  houseSavings: number
  investTotal: number
  totalAssets: number
  snapshot: MonthlySnapshot | null
  todos: MonthlyTodo[]
  memo: string
  bankBalances: { account: Account; balance: number }[]
}

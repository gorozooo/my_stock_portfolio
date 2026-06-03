import { createClient } from '@/lib/supabase/server'
import { deleteVariableExpenseAction } from '@/actions/expense'
import { OWNER_LABELS, VAR_TYPE_LABELS } from '@/lib/kakeibo/types'
import { normalizeMonth, currentMonth, formatYen, formatMonth, monthToInputValue } from '@/lib/utils'
import Link from 'next/link'
import { ChevronLeft, Trash2 } from 'lucide-react'

interface PageProps {
  searchParams: Promise<{ month?: string; owner?: string }>
}

const VAR_TYPE_COLORS: Record<string, string> = {
  CARD: 'bg-red-100 text-red-700',
  ADVANCE: 'bg-amber-100 text-amber-700',
  PENSION: 'bg-purple-100 text-purple-700',
  OTHER: 'bg-slate-100 text-slate-700',
}

export default async function ManageVariablePage({ searchParams }: PageProps) {
  const params = await searchParams
  const month = normalizeMonth(params.month ?? currentMonth().substring(0, 7)) || currentMonth()
  const ownerFilter = params.owner

  const supabase = await createClient()
  let query = supabase
    .from('monthly_variable_expenses')
    .select('*, category:categories(name), card:accounts(name)')
    .eq('month', month)
    .order('id', { ascending: false })
  if (ownerFilter) query = query.eq('owner', ownerFilter)
  const { data: expenses } = await query

  const total = (expenses ?? []).reduce((s: number, e: { amount: number }) => s + e.amount, 0)

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-3 pt-2">
        <Link href="/manage" className="text-[var(--kb-muted)]"><ChevronLeft size={20} /></Link>
        <h1 className="text-lg font-bold text-[var(--kb-text)]">変動費データ管理</h1>
      </div>

      <form method="get" className="card p-3 flex gap-2 flex-wrap">
        <input type="month" name="month" defaultValue={monthToInputValue(month)}
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
        <select name="owner" defaultValue={ownerFilter ?? ''} className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
          <option value="">全員</option>
          {(['HOUSE', 'B', 'G'] as const).map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
        </select>
        <button type="submit" className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg">絞り込み</button>
      </form>

      <div className="flex justify-between text-sm px-1">
        <span className="text-[var(--kb-muted)]">{formatMonth(month)} 変動費合計</span>
        <span className="font-semibold text-[var(--kb-expense)]">{formatYen(total)}</span>
      </div>

      <div className="card divide-y">
        {(!expenses || expenses.length === 0) && (
          <p className="text-sm text-[var(--kb-muted)] text-center py-6">データがありません</p>
        )}
        {(expenses ?? []).map((item: { id: number; owner: string; var_type: string; amount: number; memo: string; category: { name: string } | null; card: { name: string } | null }) => (
          <div key={item.id} className="flex items-center gap-3 px-4 py-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${VAR_TYPE_COLORS[item.var_type] ?? 'bg-slate-100 text-slate-700'}`}>
                  {VAR_TYPE_LABELS[item.var_type as keyof typeof VAR_TYPE_LABELS] ?? item.var_type}
                </span>
                <span className="text-xs text-[var(--kb-muted)]">{OWNER_LABELS[item.owner as keyof typeof OWNER_LABELS]}</span>
              </div>
              <p className="text-sm text-[var(--kb-text)] truncate">{item.card?.name ?? item.category?.name ?? ''}{item.memo ? ` · ${item.memo}` : ''}</p>
            </div>
            <span className="text-sm font-semibold text-[var(--kb-expense)]">{formatYen(item.amount)}</span>
            <form action={async () => { 'use server'; await deleteVariableExpenseAction(item.id) }}>
              <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
            </form>
          </div>
        ))}
      </div>
    </div>
  )
}

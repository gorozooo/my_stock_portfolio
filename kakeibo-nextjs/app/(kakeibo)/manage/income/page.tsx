import { createClient } from '@/lib/supabase/server'
import { deleteIncomeAction } from '@/actions/income'
import { OWNER_LABELS } from '@/lib/kakeibo/types'
import { normalizeMonth, currentMonth, formatYen, formatMonth, monthToInputValue } from '@/lib/utils'
import Link from 'next/link'
import { ChevronLeft, Trash2 } from 'lucide-react'

interface PageProps {
  searchParams: Promise<{ month?: string; owner?: string }>
}

export default async function ManageIncomePage({ searchParams }: PageProps) {
  const params = await searchParams
  const month = normalizeMonth(params.month ?? currentMonth().substring(0, 7)) || currentMonth()
  const ownerFilter = params.owner

  const supabase = await createClient()
  let query = supabase
    .from('monthly_incomes')
    .select('*, category:categories(name)')
    .eq('month', month)
    .order('id', { ascending: false })
  if (ownerFilter) query = query.eq('owner', ownerFilter)
  const { data: incomes } = await query

  const total = (incomes ?? []).reduce((s: number, i: { amount: number }) => s + i.amount, 0)

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-3 pt-2">
        <Link href="/manage" className="text-[var(--kb-muted)]"><ChevronLeft size={20} /></Link>
        <h1 className="text-lg font-bold text-[var(--kb-text)]">収入データ管理</h1>
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
        <span className="text-[var(--kb-muted)]">{formatMonth(month)} 収入合計</span>
        <span className="font-semibold text-[var(--kb-income)]">{formatYen(total)}</span>
      </div>

      <div className="card divide-y">
        {(!incomes || incomes.length === 0) && (
          <p className="text-sm text-[var(--kb-muted)] text-center py-6">データがありません</p>
        )}
        {(incomes ?? []).map((item: { id: number; owner: string; amount: number; memo: string; category: { name: string } | null }) => (
          <div key={item.id} className="flex items-center gap-3 px-4 py-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[var(--kb-text)]">{item.category?.name ?? '不明'}</p>
              <p className="text-xs text-[var(--kb-muted)]">{OWNER_LABELS[item.owner as keyof typeof OWNER_LABELS]}{item.memo ? ` · ${item.memo}` : ''}</p>
            </div>
            <span className="text-sm font-semibold text-[var(--kb-income)]">{formatYen(item.amount)}</span>
            <form action={async () => { 'use server'; await deleteIncomeAction(item.id) }}>
              <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
            </form>
          </div>
        ))}
      </div>
    </div>
  )
}

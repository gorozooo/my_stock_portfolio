import { createClient } from '@/lib/supabase/server'
import { deleteBankBalanceAction } from '@/actions/bank'
import { OWNER_LABELS } from '@/lib/kakeibo/types'
import { normalizeMonth, currentMonth, formatYen, formatMonth, monthToInputValue } from '@/lib/utils'
import Link from 'next/link'
import { ChevronLeft, Trash2 } from 'lucide-react'

interface PageProps {
  searchParams: Promise<{ month?: string }>
}

export default async function ManageBankPage({ searchParams }: PageProps) {
  const params = await searchParams
  const month = normalizeMonth(params.month ?? currentMonth().substring(0, 7)) || currentMonth()

  const supabase = await createClient()
  const { data: balances } = await supabase
    .from('bank_balances')
    .select('*, account:accounts(name, owner, kind)')
    .eq('month', month)
    .order('account_id')

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-3 pt-2">
        <Link href="/manage" className="text-[var(--kb-muted)]"><ChevronLeft size={20} /></Link>
        <h1 className="text-lg font-bold text-[var(--kb-text)]">口座残高データ管理</h1>
      </div>

      <form method="get" className="card p-3 flex gap-2">
        <input type="month" name="month" defaultValue={monthToInputValue(month)}
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
        <button type="submit" className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg">絞り込み</button>
      </form>

      <div className="text-sm text-[var(--kb-muted)] px-1">{formatMonth(month)}</div>

      <div className="card divide-y">
        {(!balances || balances.length === 0) && (
          <p className="text-sm text-[var(--kb-muted)] text-center py-6">データがありません</p>
        )}
        {(balances ?? []).map((item: { id: number; balance: number; account: { name: string; owner: string; kind: string } | null }) => (
          <div key={item.id} className="flex items-center gap-3 px-4 py-3">
            <div className="flex-1">
              <p className="text-sm text-[var(--kb-text)]">{item.account?.name ?? '不明'}</p>
              <p className="text-xs text-[var(--kb-muted)]">{OWNER_LABELS[item.account?.owner as keyof typeof OWNER_LABELS] ?? ''}</p>
            </div>
            <span className="text-sm font-semibold text-[var(--kb-bank)]">{formatYen(item.balance)}</span>
            <form action={async () => { 'use server'; await deleteBankBalanceAction(item.id) }}>
              <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
            </form>
          </div>
        ))}
      </div>
    </div>
  )
}

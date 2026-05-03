import { getCategories, getAccounts } from '@/lib/kakeibo/queries'
import { createCategoryAction, deleteCategoryAction, createAccountAction, deleteAccountAction } from '@/actions/settings'
import { OWNER_LABELS, OWNERS } from '@/lib/kakeibo/types'
import Link from 'next/link'
import { ChevronLeft, Trash2 } from 'lucide-react'

interface PageProps {
  searchParams: Promise<{ tab?: string }>
}

export default async function SettingsEditPage({ searchParams }: PageProps) {
  const params = await searchParams
  const tab = params.tab ?? 'income'

  const [incomeCategories, expenseCategories, accounts, cards] = await Promise.all([
    getCategories('INCOME'),
    getCategories('EXPENSE'),
    getAccounts('ACCOUNT'),
    getAccounts('CARD'),
  ])

  const tabs = [
    { id: 'income', label: '収入カテゴリ' },
    { id: 'expense', label: '支出カテゴリ' },
    { id: 'account', label: '口座' },
    { id: 'card', label: 'カード' },
  ]

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-3 pt-2">
        <Link href="/settings" className="text-[var(--kb-muted)] hover:text-[var(--kb-text)]">
          <ChevronLeft size={20} />
        </Link>
        <h1 className="text-lg font-bold text-[var(--kb-text)]">設定編集</h1>
      </div>

      {/* Tab nav */}
      <div className="flex gap-1 bg-[var(--kb-surface2)] p-1 rounded-xl">
        {tabs.map(t => (
          <Link key={t.id} href={`/settings/edit?tab=${t.id}`}
            className={`flex-1 text-center text-xs py-2 rounded-lg font-medium transition-colors ${
              tab === t.id ? 'bg-white text-[var(--kb-text)] shadow-sm' : 'text-[var(--kb-muted)]'
            }`}>
            {t.label}
          </Link>
        ))}
      </div>

      {/* Income Categories */}
      {tab === 'income' && (
        <div className="space-y-3">
          <form action={async (fd: FormData) => { 'use server'; fd.set('type', 'INCOME'); await createCategoryAction(null, fd) }} className="card p-3 flex gap-2">
            <input type="text" name="name" required placeholder="カテゴリ名" className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
            <button type="submit" className="bg-green-600 text-white text-sm px-4 py-2 rounded-lg font-medium">追加</button>
          </form>
          <div className="card divide-y">
            {incomeCategories.map(c => (
              <div key={c.id} className="flex items-center justify-between px-4 py-3">
                <span className="text-sm text-[var(--kb-text)]">{c.name}</span>
                <form action={async () => { 'use server'; await deleteCategoryAction(c.id) }}>
                  <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
                </form>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Expense Categories */}
      {tab === 'expense' && (
        <div className="space-y-3">
          <form action={async (fd: FormData) => { 'use server'; fd.set('type', 'EXPENSE'); await createCategoryAction(null, fd) }} className="card p-3 flex gap-2">
            <input type="text" name="name" required placeholder="カテゴリ名" className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
            <button type="submit" className="bg-red-500 text-white text-sm px-4 py-2 rounded-lg font-medium">追加</button>
          </form>
          <div className="card divide-y">
            {expenseCategories.map(c => (
              <div key={c.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <span className="text-sm text-[var(--kb-text)]">{c.name}</span>
                  {c.code && <span className="ml-2 text-xs text-[var(--kb-muted)] bg-slate-100 px-1.5 py-0.5 rounded">{c.code}</span>}
                </div>
                <form action={async () => { 'use server'; await deleteCategoryAction(c.id) }}>
                  <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
                </form>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Accounts */}
      {tab === 'account' && (
        <div className="space-y-3">
          <form action={async (fd: FormData) => { 'use server'; fd.set('kind', 'ACCOUNT'); await createAccountAction(null, fd) }} className="card p-3 space-y-2">
            <div className="flex gap-2">
              <select name="owner" className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                {OWNERS.map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
              </select>
              <input type="text" name="name" required placeholder="口座名" className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
              <button type="submit" className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg font-medium">追加</button>
            </div>
          </form>
          <div className="card divide-y">
            {accounts.map(a => (
              <div key={a.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <span className="text-sm text-[var(--kb-text)]">{a.name}</span>
                  <span className="ml-2 text-xs text-[var(--kb-muted)]">{OWNER_LABELS[a.owner]}</span>
                </div>
                <form action={async () => { 'use server'; await deleteAccountAction(a.id) }}>
                  <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
                </form>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cards */}
      {tab === 'card' && (
        <div className="space-y-3">
          <form action={async (fd: FormData) => { 'use server'; fd.set('kind', 'CARD'); await createAccountAction(null, fd) }} className="card p-3 space-y-2">
            <div className="flex gap-2">
              <select name="owner" className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                {OWNERS.map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
              </select>
              <input type="text" name="name" required placeholder="カード名" className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
              <button type="submit" className="bg-purple-600 text-white text-sm px-4 py-2 rounded-lg font-medium">追加</button>
            </div>
          </form>
          <div className="card divide-y">
            {cards.map(a => (
              <div key={a.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <span className="text-sm text-[var(--kb-text)]">{a.name}</span>
                  <span className="ml-2 text-xs text-[var(--kb-muted)]">{OWNER_LABELS[a.owner]}</span>
                </div>
                <form action={async () => { 'use server'; await deleteAccountAction(a.id) }}>
                  <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
                </form>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

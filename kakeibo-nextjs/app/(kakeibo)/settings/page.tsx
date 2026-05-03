import Link from 'next/link'
import { ChevronRight, Tag, Landmark, BarChart2, Database } from 'lucide-react'

export default function SettingsPage() {
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-lg font-bold text-[var(--kb-text)] pt-2">設定</h1>
      <div className="card divide-y">
        <Link href="/settings/edit?tab=income" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-green-100 flex items-center justify-center">
            <Tag size={18} className="text-[var(--kb-income)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">収入カテゴリ</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
        <Link href="/settings/edit?tab=expense" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-red-100 flex items-center justify-center">
            <Tag size={18} className="text-[var(--kb-expense)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">支出カテゴリ</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
        <Link href="/settings/edit?tab=account" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-blue-100 flex items-center justify-center">
            <Landmark size={18} className="text-[var(--kb-bank)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">銀行口座</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
        <Link href="/settings/edit?tab=card" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-purple-100 flex items-center justify-center">
            <Database size={18} className="text-[var(--kb-fixed)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">クレジットカード</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
        <Link href="/manage" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-slate-100 flex items-center justify-center">
            <BarChart2 size={18} className="text-[var(--kb-muted)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">データ管理</p>
            <p className="text-xs text-[var(--kb-muted)]">収支データの編集・削除</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
        <Link href="/charts" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-indigo-100 flex items-center justify-center">
            <BarChart2 size={18} className="text-indigo-600" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">チャート</p>
            <p className="text-xs text-[var(--kb-muted)]">12ヶ月のトレンドグラフ</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
      </div>
    </div>
  )
}

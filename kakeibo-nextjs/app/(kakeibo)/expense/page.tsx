import Link from 'next/link'
import { ChevronRight, Tag, CreditCard } from 'lucide-react'

export default function ExpensePage() {
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-lg font-bold text-[var(--kb-text)] pt-2">支出</h1>
      <div className="card divide-y">
        <Link href="/expense/variable" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-red-100 flex items-center justify-center">
            <CreditCard size={18} className="text-[var(--kb-expense)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">変動費</p>
            <p className="text-xs text-[var(--kb-muted)]">カード・立替・年金・その他</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
        <Link href="/expense/fixed" className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
          <div className="w-9 h-9 rounded-xl bg-purple-100 flex items-center justify-center">
            <Tag size={18} className="text-[var(--kb-fixed)]" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-[var(--kb-text)]">固定費</p>
            <p className="text-xs text-[var(--kb-muted)]">毎月の定期支出テンプレート</p>
          </div>
          <ChevronRight size={16} className="text-[var(--kb-muted)]" />
        </Link>
      </div>
    </div>
  )
}

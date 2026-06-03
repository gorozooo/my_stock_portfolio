import Link from 'next/link'
import { ChevronRight } from 'lucide-react'

const items = [
  { href: '/manage/income', label: '収入データ', color: 'bg-green-100 text-green-700' },
  { href: '/manage/variable', label: '変動費データ', color: 'bg-red-100 text-red-700' },
  { href: '/manage/fixed', label: '固定費テンプレート', color: 'bg-purple-100 text-purple-700' },
  { href: '/manage/bank', label: '口座残高データ', color: 'bg-blue-100 text-blue-700' },
]

export default function ManagePage() {
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-lg font-bold text-[var(--kb-text)] pt-2">データ管理</h1>
      <div className="card divide-y">
        {items.map(item => (
          <Link key={item.href} href={item.href} className="flex items-center gap-3 px-4 py-4 hover:bg-[var(--kb-surface2)] transition-colors">
            <span className={`text-xs font-semibold px-2 py-1 rounded-full ${item.color}`}>{item.label}</span>
            <span className="flex-1" />
            <ChevronRight size={16} className="text-[var(--kb-muted)]" />
          </Link>
        ))}
      </div>
    </div>
  )
}

import { getCategories } from '@/lib/kakeibo/queries'
import IncomeForm from '@/components/forms/IncomeForm'
import Link from 'next/link'
import { Settings } from 'lucide-react'

export default async function IncomePage() {
  const categories = await getCategories('INCOME')

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-[var(--kb-text)]">収入入力</h1>
        <Link href="/manage/income" className="text-sm text-blue-600 hover:underline flex items-center gap-1">
          <Settings size={14} />
          管理
        </Link>
      </div>
      <IncomeForm categories={categories} />
    </div>
  )
}

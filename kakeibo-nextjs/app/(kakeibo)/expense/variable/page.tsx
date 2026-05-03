import { getAccounts } from '@/lib/kakeibo/queries'
import VariableExpenseForm from '@/components/forms/VariableExpenseForm'
import Link from 'next/link'
import { Settings } from 'lucide-react'

export default async function VariableExpensePage() {
  const cards = await getAccounts('CARD')

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-[var(--kb-text)]">変動費入力</h1>
        <Link href="/manage/variable" className="text-sm text-blue-600 hover:underline flex items-center gap-1">
          <Settings size={14} />
          管理
        </Link>
      </div>
      <VariableExpenseForm allCards={cards} />
    </div>
  )
}

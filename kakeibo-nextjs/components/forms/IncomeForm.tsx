'use client'
import { useFormState } from 'react-dom'
import { useEffect } from 'react'
import { createIncomeAction } from '@/actions/income'
import { toast } from 'sonner'
import { monthToInputValue, currentMonth } from '@/lib/utils'
import { OWNERS, OWNER_LABELS } from '@/lib/kakeibo/types'
import type { Category } from '@/lib/kakeibo/types'
import SubmitButton from '@/components/ui/SubmitButton'

interface Props {
  categories: Category[]
}

export default function IncomeForm({ categories }: Props) {
  const [state, formAction] = useFormState(createIncomeAction, null)

  useEffect(() => {
    if (state?.success) toast.success((state as { success: boolean; message?: string }).message ?? '登録しました')
    if (state?.error) toast.error(state.error)
  }, [state])

  return (
    <form action={formAction} className="card p-4 space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>月</label>
        <input type="month" name="month" defaultValue={monthToInputValue(currentMonth())}
          className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>対象者</label>
        <select name="owner" className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
          {OWNERS.map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>カテゴリ</label>
        <select name="category_id" className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
          <option value="">選択してください</option>
          {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>金額（円）</label>
        <input type="number" name="amount" min="0" step="1"
          className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required placeholder="0" />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>メモ</label>
        <input type="text" name="memo"
          className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" placeholder="任意" />
      </div>
      <SubmitButton label="収入を登録" loadingLabel="登録中..." className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-green-700" />
    </form>
  )
}

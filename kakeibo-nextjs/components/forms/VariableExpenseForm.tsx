'use client'
import { useFormState } from 'react-dom'
import { useEffect, useState } from 'react'
import { createVariableExpenseAction } from '@/actions/expense'
import { toast } from 'sonner'
import { monthToInputValue, currentMonth } from '@/lib/utils'
import { OWNERS, OWNER_LABELS, VAR_TYPE_LABELS } from '@/lib/kakeibo/types'
import type { Account, OwnerType } from '@/lib/kakeibo/types'
import SubmitButton from '@/components/ui/SubmitButton'

interface Props {
  allCards: Account[]
}

const VAR_TYPES = ['CARD', 'ADVANCE', 'PENSION', 'OTHER'] as const
const VAR_TYPE_COLORS: Record<string, string> = {
  CARD: 'var(--kb-expense)',
  ADVANCE: 'var(--kb-advance)',
  PENSION: 'var(--kb-fixed)',
  OTHER: 'var(--kb-muted)',
}

export default function VariableExpenseForm({ allCards }: Props) {
  const [state, formAction] = useFormState(createVariableExpenseAction, null)
  const [selectedOwner, setSelectedOwner] = useState<OwnerType>('HOUSE')
  const [selectedVarType, setSelectedVarType] = useState('CARD')

  const filteredCards = allCards.filter(c => c.owner === selectedOwner)

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
        <select name="owner" value={selectedOwner} onChange={e => setSelectedOwner(e.target.value as OwnerType)}
          className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
          {OWNERS.map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>種別</label>
        <div className="grid grid-cols-4 gap-2">
          {VAR_TYPES.map(vt => (
            <label key={vt} className="flex flex-col items-center justify-center border rounded-lg p-2 cursor-pointer transition-colors"
              style={{ background: selectedVarType === vt ? '#eff6ff' : 'white', borderColor: selectedVarType === vt ? '#60a5fa' : '#e2e8f0' }}>
              <input type="radio" name="var_type" value={vt} checked={selectedVarType === vt}
                onChange={() => setSelectedVarType(vt)} className="sr-only" />
              <span className="text-xs font-semibold" style={{ color: VAR_TYPE_COLORS[vt] }}>{VAR_TYPE_LABELS[vt]}</span>
            </label>
          ))}
        </div>
      </div>
      {selectedVarType === 'CARD' && (
        <div>
          <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>カード</label>
          <select name="card_id" className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
            <option value="">選択してください</option>
            {filteredCards.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {filteredCards.length === 0 && <p className="text-xs mt-1" style={{ color: 'var(--kb-muted)' }}>この方のカードが未登録です。設定から追加してください。</p>}
        </div>
      )}
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
      <SubmitButton label="変動費を登録" loadingLabel="登録中..." className="w-full bg-red-500 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-red-600" />
    </form>
  )
}

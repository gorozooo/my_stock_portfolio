'use client'
import { useFormState } from 'react-dom'
import { useEffect, useState } from 'react'
import { upsertBankBalanceAction } from '@/actions/bank'
import { toast } from 'sonner'
import { monthToInputValue, currentMonth } from '@/lib/utils'
import { OWNERS, OWNER_LABELS } from '@/lib/kakeibo/types'
import type { Account, OwnerType } from '@/lib/kakeibo/types'
import SubmitButton from '@/components/ui/SubmitButton'

interface Props {
  allAccounts: Account[]
}

export default function BankBalanceForm({ allAccounts }: Props) {
  const [state, formAction] = useFormState(upsertBankBalanceAction, null)
  const [selectedOwner, setSelectedOwner] = useState<OwnerType>('HOUSE')

  const filtered = allAccounts.filter(a => a.kind === 'ACCOUNT' && a.owner === selectedOwner)

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
        <select value={selectedOwner} onChange={e => setSelectedOwner(e.target.value as OwnerType)}
          className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
          {OWNERS.map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>口座</label>
        <select name="account_id" className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
          <option value="">選択してください</option>
          {filtered.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        {filtered.length === 0 && <p className="text-xs mt-1" style={{ color: 'var(--kb-muted)' }}>この方の口座が未登録です。設定から追加してください。</p>}
      </div>
      <div>
        <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>残高（円）</label>
        <input type="number" name="balance" min="0" step="1" required placeholder="0"
          className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
      </div>
      <SubmitButton label="残高を登録" loadingLabel="登録中..." className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-blue-700" />
    </form>
  )
}

'use client'
import { useFormState } from 'react-dom'
import { useEffect, useTransition } from 'react'
import { createFixedTemplateAction, updateFixedTemplateAction, deleteFixedTemplateAction } from '@/actions/expense'
import { toast } from 'sonner'
import { OWNERS, OWNER_LABELS } from '@/lib/kakeibo/types'
import type { Category, FixedExpenseTemplate } from '@/lib/kakeibo/types'
import { formatYen } from '@/lib/utils'
import { Trash2, ToggleLeft, ToggleRight } from 'lucide-react'
import SubmitButton from '@/components/ui/SubmitButton'

interface Props {
  categories: Category[]
  templates: FixedExpenseTemplate[]
}

export default function FixedExpenseForm({ categories, templates }: Props) {
  const [state, formAction] = useFormState(createFixedTemplateAction, null)
  const [pending, startTransition] = useTransition()

  useEffect(() => {
    if (state?.success) toast.success((state as { success?: boolean; message?: string }).message ?? '登録しました')
    if (state?.error) toast.error(state.error)
  }, [state])

  function toggleActive(t: FixedExpenseTemplate) {
    const fd = new FormData()
    fd.set('amount', String(t.amount))
    fd.set('memo', t.memo)
    fd.set('is_active', String(!t.is_active))
    fd.set('category_id', String(t.category_id))
    startTransition(async () => {
      const res = await updateFixedTemplateAction(t.id, fd)
      if (res?.error) toast.error(res.error)
      else toast.success(t.is_active ? '無効にしました' : '有効にしました')
    })
  }

  function handleDelete(id: number) {
    startTransition(async () => {
      const res = await deleteFixedTemplateAction(id)
      if (res?.error) toast.error(res.error)
      else toast.success('削除しました')
    })
  }

  const byOwner = (['HOUSE', 'B', 'G'] as const).map(owner => ({
    owner,
    items: templates.filter(t => t.owner === owner),
  }))

  return (
    <div className="space-y-5">
      {/* Add form */}
      <form action={formAction} className="card p-4 space-y-4">
        <h2 className="font-semibold text-[var(--kb-text)]">固定費を追加</h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-[var(--kb-muted)] mb-1">対象者</label>
            <select name="owner" className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
              {OWNERS.map(o => <option key={o} value={o}>{OWNER_LABELS[o]}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--kb-muted)] mb-1">カテゴリ</label>
            <select name="category_id" className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" required>
              <option value="">選択</option>
              {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-[var(--kb-muted)] mb-1">金額（円）</label>
            <input type="number" name="amount" min="0" required placeholder="0"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--kb-muted)] mb-1">メモ</label>
            <input type="text" name="memo" placeholder="任意"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white" />
          </div>
        </div>
        <SubmitButton label="固定費を追加" loadingLabel="追加中..." className="w-full bg-purple-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-purple-700" />
      </form>

      {/* List by owner */}
      {byOwner.map(({ owner, items }) => (
        <div key={owner}>
          <p className="section-title">{OWNER_LABELS[owner]}</p>
          {items.length === 0 ? (
            <p className="text-sm text-[var(--kb-muted)] text-center py-3">なし</p>
          ) : (
            <div className="card divide-y">
              {items.map(t => (
                <div key={t.id} className={`flex items-center gap-3 px-4 py-3 ${!t.is_active ? 'opacity-40' : ''}`}>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-[var(--kb-text)] truncate">{t.category?.name}{t.memo ? ` (${t.memo})` : ''}</p>
                    <p className="text-xs text-[var(--kb-fixed)] font-semibold">{formatYen(t.amount)}</p>
                  </div>
                  <button onClick={() => toggleActive(t)} className={`transition-colors ${t.is_active ? 'text-green-500' : 'text-slate-300'}`}>
                    {t.is_active ? <ToggleRight size={24} /> : <ToggleLeft size={24} />}
                  </button>
                  <button onClick={() => handleDelete(t.id)} className="text-[var(--kb-muted)] hover:text-red-500 transition-colors p-1">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <div className="flex justify-between px-4 py-2 bg-[var(--kb-surface2)]">
                <span className="text-xs text-[var(--kb-muted)]">有効合計</span>
                <span className="text-xs font-semibold text-[var(--kb-fixed)]">
                  {formatYen(items.filter(t => t.is_active).reduce((s, t) => s + t.amount, 0))}
                </span>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

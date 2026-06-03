import { getFixedTemplates } from '@/lib/kakeibo/queries'
import { deleteFixedTemplateAction, updateFixedTemplateAction } from '@/actions/expense'
import { OWNER_LABELS } from '@/lib/kakeibo/types'
import { formatYen } from '@/lib/utils'
import Link from 'next/link'
import { ChevronLeft, Trash2 } from 'lucide-react'

export default async function ManageFixedPage() {
  const templates = await getFixedTemplates()

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-3 pt-2">
        <Link href="/manage" className="text-[var(--kb-muted)]"><ChevronLeft size={20} /></Link>
        <h1 className="text-lg font-bold text-[var(--kb-text)]">固定費テンプレート管理</h1>
      </div>
      <div className="card divide-y">
        {templates.length === 0 && <p className="text-sm text-[var(--kb-muted)] text-center py-6">データがありません</p>}
        {templates.map(t => (
          <div key={t.id} className={`flex items-center gap-3 px-4 py-3 ${!t.is_active ? 'opacity-50' : ''}`}>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-xs text-[var(--kb-muted)]">{OWNER_LABELS[t.owner]}</span>
                {!t.is_active && <span className="text-xs bg-slate-100 text-slate-500 px-1.5 rounded">無効</span>}
              </div>
              <p className="text-sm text-[var(--kb-text)] truncate">{t.category?.name}{t.memo ? ` (${t.memo})` : ''}</p>
              <p className="text-xs font-semibold text-[var(--kb-fixed)]">{formatYen(t.amount)}</p>
            </div>
            <form action={async (fd: FormData) => {
              'use server'
              fd.set('amount', String(t.amount))
              fd.set('memo', t.memo)
              fd.set('is_active', String(!t.is_active))
              fd.set('category_id', String(t.category_id))
              await updateFixedTemplateAction(t.id, fd)
            }}>
              <button type="submit" className={`text-xs px-2 py-1 rounded border font-medium transition-colors ${
                t.is_active ? 'border-green-300 text-green-700' : 'border-slate-300 text-slate-500'
              }`}>{t.is_active ? '有効' : '無効'}</button>
            </form>
            <form action={async () => { 'use server'; await deleteFixedTemplateAction(t.id) }}>
              <button type="submit" className="text-[var(--kb-muted)] hover:text-red-500 p-1"><Trash2 size={14} /></button>
            </form>
          </div>
        ))}
      </div>
    </div>
  )
}

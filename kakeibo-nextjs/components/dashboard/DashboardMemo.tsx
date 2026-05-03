'use client'
import { useState, useRef, useTransition } from 'react'
import { saveMemoAction } from '@/actions/memo'
import { toast } from 'sonner'

interface Props {
  month: string
  initialMemo: string
}

export default function DashboardMemo({ month, initialMemo }: Props) {
  const [memo, setMemo] = useState(initialMemo)
  const [isPending, startTransition] = useTransition()
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleChange(val: string) {
    setMemo(val)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      startTransition(async () => {
        const res = await saveMemoAction(month, val)
        if (res?.error) toast.error(res.error)
      })
    }, 800)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="section-title">メモ</span>
        {isPending && <span className="text-xs text-[var(--kb-muted)]">保存中...</span>}
      </div>
      <div className="card p-3">
        <textarea
          value={memo}
          onChange={e => handleChange(e.target.value)}
          className="w-full text-sm resize-none focus:outline-none bg-transparent text-[var(--kb-text)] placeholder:text-[var(--kb-muted)]"
          rows={3}
          placeholder="今月のメモを入力..."
        />
      </div>
    </div>
  )
}

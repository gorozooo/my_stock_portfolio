'use client'
import { useTransition } from 'react'
import { confirmSnapshotAction } from '@/actions/snapshot'
import { toast } from 'sonner'
import { Lock, RefreshCw } from 'lucide-react'

interface Props {
  month: string
  hasSnapshot: boolean
}

export default function SnapshotButton({ month, hasSnapshot }: Props) {
  const [isPending, startTransition] = useTransition()

  function handleConfirm() {
    startTransition(async () => {
      const res = await confirmSnapshotAction(month)
      if (res?.error) toast.error(res.error)
      else toast.success('今月の収支を確定しました')
    })
  }

  return (
    <button
      onClick={handleConfirm}
      disabled={isPending}
      className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium transition-colors disabled:opacity-50 ${
        hasSnapshot
          ? 'bg-green-100 text-green-700 hover:bg-green-200'
          : 'bg-blue-100 text-blue-700 hover:bg-blue-200'
      }`}
    >
      {hasSnapshot ? <RefreshCw size={12} /> : <Lock size={12} />}
      {hasSnapshot ? '再確定' : '今月を確定'}
    </button>
  )
}

'use client'
import { useRouter, usePathname, useSearchParams } from 'next/navigation'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { formatMonth, addMonths, subtractMonths, currentMonth } from '@/lib/utils'

interface Props {
  month: string
}

export default function MonthSelector({ month }: Props) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  function navigate(newMonth: string) {
    const params = new URLSearchParams(searchParams.toString())
    params.set('month', newMonth.substring(0, 7))
    router.push(`${pathname}?${params.toString()}`)
  }

  const isCurrentMonth = month.substring(0, 7) === currentMonth().substring(0, 7)

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => navigate(subtractMonths(month, 1))}
        className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors text-[var(--kb-muted)]"
      >
        <ChevronLeft size={18} />
      </button>
      <span className="font-semibold text-[var(--kb-text)] min-w-[7rem] text-center">
        {formatMonth(month)}
      </span>
      <button
        onClick={() => navigate(addMonths(month, 1))}
        disabled={isCurrentMonth}
        className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors text-[var(--kb-muted)] disabled:opacity-30"
      >
        <ChevronRight size={18} />
      </button>
    </div>
  )
}

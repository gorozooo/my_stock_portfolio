'use client'
import { useFormStatus } from 'react-dom'
import { cn } from '@/lib/utils'

interface Props {
  label: string
  loadingLabel?: string
  className?: string
}

export default function SubmitButton({ label, loadingLabel, className }: Props) {
  const { pending } = useFormStatus()
  return (
    <button
      type="submit"
      disabled={pending}
      className={cn('disabled:opacity-50 transition-colors', className)}
    >
      {pending ? (loadingLabel ?? label) : label}
    </button>
  )
}

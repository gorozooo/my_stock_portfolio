import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { format, parseISO, startOfMonth, addMonths as dfAddMonths, subMonths } from 'date-fns'
import { ja } from 'date-fns/locale'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatYen(amount: number): string {
  return new Intl.NumberFormat('ja-JP', { style: 'currency', currency: 'JPY' }).format(amount)
}

export function formatYenShort(amount: number): string {
  return amount.toLocaleString('ja-JP') + '円'
}

export function formatMonth(month: string): string {
  try {
    return format(parseISO(month), 'yyyy年M月', { locale: ja })
  } catch {
    return month
  }
}

export function formatMonthShort(month: string): string {
  try {
    return format(parseISO(month), 'M月', { locale: ja })
  } catch {
    return month
  }
}

export function normalizeMonth(value: string): string {
  // Accepts YYYY-MM or YYYY-MM-DD, returns YYYY-MM-01
  if (!value) return ''
  const parts = value.split('-')
  if (parts.length >= 2) return `${parts[0]}-${parts[1].padStart(2, '0')}-01`
  return ''
}

export function currentMonth(): string {
  return format(startOfMonth(new Date()), 'yyyy-MM-dd')
}

export function addMonths(month: string, n: number): string {
  try {
    return format(dfAddMonths(parseISO(month), n), 'yyyy-MM-dd')
  } catch {
    return month
  }
}

export function subtractMonths(month: string, n: number): string {
  try {
    return format(subMonths(parseISO(month), n), 'yyyy-MM-dd')
  } catch {
    return month
  }
}

export function monthToInputValue(month: string): string {
  // Convert YYYY-MM-01 to YYYY-MM for <input type="month">
  if (!month) return ''
  return month.substring(0, 7)
}

export function getLast12Months(): string[] {
  const months: string[] = []
  for (let i = 11; i >= 0; i--) {
    months.push(format(subMonths(startOfMonth(new Date()), i), 'yyyy-MM-dd'))
  }
  return months
}

export function diffLabel(diff: number): string {
  if (diff > 0) return `+${diff.toLocaleString('ja-JP')}円`
  return `${diff.toLocaleString('ja-JP')}円`
}

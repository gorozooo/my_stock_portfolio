'use server'
import { revalidatePath } from 'next/cache'
import { redirect } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import { normalizeMonth } from '@/lib/utils'

async function requireAuth() {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')
  return supabase
}

export async function saveMemoAction(month: string, memo: string) {
  const supabase = await requireAuth()
  const m = normalizeMonth(month)
  const { error } = await supabase.from('monthly_dashboard_memos').upsert(
    { month: m, memo, updated_at: new Date().toISOString() },
    { onConflict: 'month' }
  )
  if (error) return { error: error.message }
  revalidatePath('/dashboard')
  return { success: true }
}

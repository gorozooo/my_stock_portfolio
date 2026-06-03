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

export async function addTodoAction(month: string, title: string) {
  const supabase = await requireAuth()
  const m = normalizeMonth(month)
  const { error } = await supabase.from('monthly_todos').insert({ month: m, title, note: '', status: 'TODO', sort_order: 0 })
  if (error) return { error: error.message }
  revalidatePath('/dashboard')
  return { success: true }
}

export async function toggleTodoAction(id: number, currentStatus: string) {
  const supabase = await requireAuth()
  const newStatus = currentStatus === 'DONE' ? 'TODO' : 'DONE'
  const { error } = await supabase.from('monthly_todos').update({
    status: newStatus,
    completed_at: newStatus === 'DONE' ? new Date().toISOString() : null
  }).eq('id', id)
  if (error) return { error: error.message }
  revalidatePath('/dashboard')
  return { success: true }
}

export async function deleteTodoAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('monthly_todos').delete().eq('id', id)
  if (error) return { error: error.message }
  revalidatePath('/dashboard')
  return { success: true }
}

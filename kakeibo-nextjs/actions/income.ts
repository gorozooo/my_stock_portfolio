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

export async function createIncomeAction(prevState: unknown, formData: FormData) {
  const supabase = await requireAuth()
  const month = normalizeMonth(formData.get('month') as string)
  const owner = formData.get('owner') as string
  const category_id = Number(formData.get('category_id'))
  const amount = Number(formData.get('amount'))
  const memo = (formData.get('memo') as string) ?? ''

  if (!month || !owner || !category_id || !amount) return { error: '入力が不足しています' }

  const { error } = await supabase.from('monthly_incomes').insert({ month, owner, category_id, amount, memo })
  if (error) return { error: '登録に失敗しました: ' + error.message }

  revalidatePath('/dashboard')
  revalidatePath('/income')
  revalidatePath('/manage/income')
  revalidatePath('/analysis')
  return { success: true, message: `${amount.toLocaleString()}円 登録しました` }
}

export async function updateIncomeAction(id: number, formData: FormData) {
  const supabase = await requireAuth()
  const amount = Number(formData.get('amount'))
  const memo = (formData.get('memo') as string) ?? ''
  const category_id = Number(formData.get('category_id'))

  const { error } = await supabase.from('monthly_incomes').update({ amount, memo, category_id }).eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/manage/income')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true }
}

export async function deleteIncomeAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('monthly_incomes').delete().eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/manage/income')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true }
}

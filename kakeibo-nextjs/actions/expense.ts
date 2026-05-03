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

async function getCategoryIdForVarType(supabase: Awaited<ReturnType<typeof createClient>>, varType: string): Promise<number | null> {
  const { data } = await supabase
    .from('categories')
    .select('id')
    .eq('type', 'EXPENSE')
    .eq('code', varType)
    .single()
  return (data as { id: number } | null)?.id ?? null
}

export async function createVariableExpenseAction(prevState: unknown, formData: FormData) {
  const supabase = await requireAuth()
  const month = normalizeMonth(formData.get('month') as string)
  const owner = formData.get('owner') as string
  const var_type = formData.get('var_type') as string
  const card_id = formData.get('card_id') ? Number(formData.get('card_id')) : null
  const amount = Number(formData.get('amount'))
  const memo = (formData.get('memo') as string) ?? ''

  if (!month || !owner || !var_type || !amount) return { error: '入力が不足しています' }
  if (var_type === 'CARD' && !card_id) return { error: 'カードを選択してください' }

  const category_id = await getCategoryIdForVarType(supabase, var_type)
  if (!category_id) return { error: 'カテゴリが見つかりません。設定からカテゴリを確認してください。' }

  const { error } = await supabase.from('monthly_variable_expenses').insert({
    month, owner, var_type, category_id, card_id: card_id ?? null, amount, memo
  })
  if (error) return { error: '登録に失敗しました: ' + error.message }

  revalidatePath('/dashboard')
  revalidatePath('/expense/variable')
  revalidatePath('/manage/variable')
  revalidatePath('/analysis')
  return { success: true, message: `${amount.toLocaleString()}円 登録しました` }
}

export async function updateVariableExpenseAction(id: number, formData: FormData) {
  const supabase = await requireAuth()
  const amount = Number(formData.get('amount'))
  const memo = (formData.get('memo') as string) ?? ''

  const { error } = await supabase.from('monthly_variable_expenses').update({ amount, memo }).eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/manage/variable')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true }
}

export async function deleteVariableExpenseAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('monthly_variable_expenses').delete().eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/manage/variable')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true }
}

export async function createFixedTemplateAction(prevState: unknown, formData: FormData) {
  const supabase = await requireAuth()
  const owner = formData.get('owner') as string
  const category_id = Number(formData.get('category_id'))
  const amount = Number(formData.get('amount'))
  const memo = (formData.get('memo') as string) ?? ''

  if (!owner || !category_id || !amount) return { error: '入力が不足しています' }

  const { error } = await supabase.from('fixed_expense_templates').insert({ owner, category_id, amount, memo, is_active: true })
  if (error) return { error: '登録に失敗しました: ' + error.message }

  revalidatePath('/expense/fixed')
  revalidatePath('/manage/fixed')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true, message: '固定費を登録しました' }
}

export async function updateFixedTemplateAction(id: number, formData: FormData) {
  const supabase = await requireAuth()
  const amount = Number(formData.get('amount'))
  const memo = (formData.get('memo') as string) ?? ''
  const is_active = formData.get('is_active') === 'true'
  const category_id = Number(formData.get('category_id'))

  const { error } = await supabase.from('fixed_expense_templates').update({ amount, memo, is_active, category_id }).eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/expense/fixed')
  revalidatePath('/manage/fixed')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true }
}

export async function deleteFixedTemplateAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('fixed_expense_templates').delete().eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/expense/fixed')
  revalidatePath('/manage/fixed')
  revalidatePath('/dashboard')
  revalidatePath('/analysis')
  return { success: true }
}

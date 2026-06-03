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

export async function upsertBankBalanceAction(prevState: unknown, formData: FormData) {
  const supabase = await requireAuth()
  const month = normalizeMonth(formData.get('month') as string)
  const account_id = Number(formData.get('account_id'))
  const balance = Number(formData.get('balance'))

  if (!month || !account_id || balance === undefined) return { error: '入力が不足しています' }

  const { error } = await supabase.from('bank_balances').upsert(
    { month, account_id, balance, updated_at: new Date().toISOString() },
    { onConflict: 'month,account_id' }
  )
  if (error) return { error: '登録に失敗しました: ' + error.message }

  revalidatePath('/bank')
  revalidatePath('/dashboard')
  revalidatePath('/manage/bank')
  return { success: true, message: `${balance.toLocaleString()}円 登録しました` }
}

export async function deleteBankBalanceAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('bank_balances').delete().eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/bank')
  revalidatePath('/dashboard')
  revalidatePath('/manage/bank')
  return { success: true }
}

'use server'
import { revalidatePath } from 'next/cache'
import { redirect } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'

async function requireAuth() {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')
  return supabase
}

// Categories
export async function createCategoryAction(prevState: unknown, formData: FormData) {
  const supabase = await requireAuth()
  const type = formData.get('type') as string
  const name = formData.get('name') as string
  const code = (formData.get('code') as string) ?? ''
  const sort_order = Number(formData.get('sort_order') ?? 0)

  if (!type || !name) return { error: '名前は必須です' }

  const { error } = await supabase.from('categories').insert({ type, name, code, sort_order })
  if (error) return { error: '登録に失敗: ' + error.message }

  revalidatePath('/settings/edit')
  return { success: true }
}

export async function updateCategoryAction(id: number, formData: FormData) {
  const supabase = await requireAuth()
  const name = formData.get('name') as string
  const sort_order = Number(formData.get('sort_order') ?? 0)

  const { error } = await supabase.from('categories').update({ name, sort_order }).eq('id', id)
  if (error) return { error: error.message }

  revalidatePath('/settings/edit')
  return { success: true }
}

export async function deleteCategoryAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('categories').delete().eq('id', id)
  if (error) return { error: '削除に失敗: ' + error.message }

  revalidatePath('/settings/edit')
  return { success: true }
}

// Accounts
export async function createAccountAction(prevState: unknown, formData: FormData) {
  const supabase = await requireAuth()
  const kind = formData.get('kind') as string
  const owner = formData.get('owner') as string
  const name = formData.get('name') as string

  if (!kind || !owner || !name) return { error: '全て入力してください' }

  const { error } = await supabase.from('accounts').insert({ kind, owner, name })
  if (error) return { error: '登録に失敗: ' + error.message }

  revalidatePath('/settings/edit')
  return { success: true }
}

export async function deleteAccountAction(id: number) {
  const supabase = await requireAuth()
  const { error } = await supabase.from('accounts').delete().eq('id', id)
  if (error) return { error: '削除に失敗: ' + error.message }

  revalidatePath('/settings/edit')
  return { success: true }
}

import { getCategories, getFixedTemplates } from '@/lib/kakeibo/queries'
import FixedExpenseForm from '@/components/forms/FixedExpenseForm'

export default async function FixedExpensePage() {
  const [categories, templates] = await Promise.all([
    getCategories('EXPENSE'),
    getFixedTemplates(),
  ])

  return (
    <div className="p-4 space-y-4">
      <h1 className="text-lg font-bold text-[var(--kb-text)] pt-2">固定費管理</h1>
      <FixedExpenseForm categories={categories} templates={templates} />
    </div>
  )
}

import { redirect } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import BottomTabNav from '@/components/layout/BottomTabNav'

export default async function KakeiboLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  return (
    <div className="min-h-screen" style={{ background: 'var(--kb-bg)' }}>
      <main className="max-w-lg mx-auto pb-24">
        {children}
      </main>
      <BottomTabNav />
    </div>
  )
}

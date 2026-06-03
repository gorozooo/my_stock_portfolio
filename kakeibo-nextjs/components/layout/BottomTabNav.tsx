'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Home, TrendingDown, TrendingUp, Landmark, BarChart2, Settings, PieChart } from 'lucide-react'
import { cn } from '@/lib/utils'

const tabs = [
  { href: '/dashboard', label: 'ホーム', icon: Home },
  { href: '/expense', label: '支出', icon: TrendingDown },
  { href: '/income', label: '収入', icon: TrendingUp },
  { href: '/bank', label: '銀行', icon: Landmark },
  { href: '/analysis', label: '分析', icon: PieChart },
  { href: '/settings', label: '設定', icon: Settings },
]

export default function BottomTabNav() {
  const pathname = usePathname()

  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-white border-t border-[var(--kb-border)] z-50"
      style={{ paddingBottom: 'max(0.5rem, env(safe-area-inset-bottom))' }}>
      <div className="max-w-lg mx-auto flex">
        {tabs.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + '/')
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'flex-1 flex flex-col items-center gap-0.5 pt-2 pb-1 text-[10px] font-medium transition-colors',
                active ? 'text-[var(--kb-accent)]' : 'text-[var(--kb-muted)]'
              )}
            >
              <Icon size={20} strokeWidth={active ? 2.5 : 2} />
              <span>{label}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}

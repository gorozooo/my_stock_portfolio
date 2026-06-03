'use client'
import { useFormState } from 'react-dom'
import { loginAction } from '@/actions/auth'
import SubmitButton from '@/components/ui/SubmitButton'

export default function LoginPage() {
  const [state, formAction] = useFormState(loginAction, null)

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--kb-bg)' }}>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="text-4xl mb-2">💰</div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--kb-text)' }}>家計簿</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--kb-muted)' }}>ログイン</p>
        </div>

        <div className="card p-6">
          <form action={formAction} className="space-y-4">
            {state?.error && (
              <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
                {state.error}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>
                メールアドレス
              </label>
              <input
                type="email"
                name="email"
                required
                autoComplete="email"
                className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
                placeholder="example@email.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--kb-text)' }}>
                パスワード
              </label>
              <input
                type="password"
                name="password"
                required
                autoComplete="current-password"
                className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
                placeholder="••••••••"
              />
            </div>
            <SubmitButton label="ログイン" loadingLabel="ログイン中..." className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-blue-700 transition-colors disabled:opacity-50 mt-2" />
          </form>
        </div>
      </div>
    </div>
  )
}

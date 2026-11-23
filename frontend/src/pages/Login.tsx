import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import * as api from '../api'

export default function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(email, password)
      navigate('/cases')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-xl font-bold text-slate-900">AI 訴訟輔助系統</h1>
        <p className="text-xs text-amber-700">開發測試環境：只可使用合成案件，不得放入真實案件資料。</p>
        <label className="block text-sm text-slate-700">
          電子郵件
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2" />
        </label>
        <label className="block text-sm text-slate-700">
          密碼
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2" />
        </label>
        {error && <p className="text-sm text-red-700">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full rounded bg-indigo-600 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
          登入
        </button>
      </form>
    </div>
  )
}

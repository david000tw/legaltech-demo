import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import * as api from '../api'
import { useMe } from '../MeContext'

// 深色頂端列（設計 5.13）：系統名稱、案件名稱、試算、用量與費用（助理看不到）、登出。
export default function AppShell({ caseTitle, caseId, children }: {
  caseTitle?: string; caseId?: string; children: ReactNode
}) {
  const me = useMe()
  const navigate = useNavigate()

  async function logout() {
    try {
      await api.logout()
    } finally {
      navigate('/login', { replace: true })
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-slate-900 text-white">
        <nav className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-1 px-6 py-3 text-sm">
          <Link to="/cases" className="text-base font-bold tracking-wide text-white">AI 訴訟輔助系統</Link>
          {caseTitle && <span className="text-amber-300">{caseTitle}</span>}
          <span className="ml-auto flex flex-wrap items-center gap-x-5">
            <Link to={caseId ? `/tools?case=${caseId}` : '/tools'} className="text-slate-200 hover:text-white">
              裁判費與期限試算
            </Link>
            {me?.role !== 'assistant' && (
              <Link to="/usage" className="text-slate-200 hover:text-white">用量與費用</Link>
            )}
            <button type="button" onClick={() => void logout()}
              className="rounded border border-slate-500 px-2 py-0.5 text-slate-200 hover:bg-slate-800">
              登出
            </button>
          </span>
        </nav>
      </header>
      {children}
    </div>
  )
}

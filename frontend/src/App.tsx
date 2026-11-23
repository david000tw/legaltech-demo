import { useEffect, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import * as api from './api'
import CaseDetail from './pages/CaseDetail'
import Cases from './pages/Cases'
import Login from './pages/Login'

function RequireLogin({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'in' | 'out'>('checking')
  useEffect(() => {
    api.me().then(() => setState('in')).catch(() => setState('out'))
  }, [])
  if (state === 'checking') return <p className="p-6 text-slate-600">載入中…</p>
  return state === 'in' ? <>{children}</> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/cases" element={<RequireLogin><Cases /></RequireLogin>} />
      <Route path="/cases/:caseId" element={<RequireLogin><CaseDetail /></RequireLogin>} />
      <Route path="*" element={<Navigate to="/cases" replace />} />
    </Routes>
  )
}

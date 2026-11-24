import { useEffect, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import * as api from './api'
import { MeContext } from './MeContext'
import CaseDetail from './pages/CaseDetail'
import Cases from './pages/Cases'
import DraftEditor from './pages/DraftEditor'
import Login from './pages/Login'
import ToolsPage from './pages/ToolsPage'
import UsagePage from './pages/UsagePage'

function RequireLogin({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<api.Me | null | 'checking' | 'out'>('checking')
  useEffect(() => {
    api.me().then(setMe).catch(() => setMe('out'))
  }, [])
  if (me === 'checking' || me === null) return <p className="p-6 text-slate-600">載入中…</p>
  if (me === 'out') return <Navigate to="/login" replace />
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/cases" element={<RequireLogin><Cases /></RequireLogin>} />
      <Route path="/cases/:caseId" element={<RequireLogin><CaseDetail /></RequireLogin>} />
      <Route path="/drafts/:draftId" element={<RequireLogin><DraftEditor /></RequireLogin>} />
      <Route path="/tools" element={<RequireLogin><ToolsPage /></RequireLogin>} />
      <Route path="/usage" element={<RequireLogin><UsagePage /></RequireLogin>} />
      <Route path="*" element={<Navigate to="/cases" replace />} />
    </Routes>
  )
}

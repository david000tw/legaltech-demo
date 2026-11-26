import { useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import * as api from '../api'
import AppShell from '../components/AppShell'
import { importHint, isUnknownCourt, judgmentPath, UNKNOWN_COURT } from '../judgmentLinks'
import { CARD } from '../ui'

// 一個被引用的判決字號（計畫 3 丙）：庫裡剛好一份就直接打開；兩份以上（同字號的判決與裁定）逐一列出、不替律師挑；
// 不在庫裡就列出引用它的庫內判決與引用的那一句，並提示可以貼網址加入
export default function CitedJudgment() {
  const { caseId = '', key = '' } = useParams()
  const [view, setView] = useState<api.CitedView | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.citedJudgment(caseId, key).then(setView).catch((e: Error) => setError(e.message))
  }, [caseId, key])

  if (view && view.library.length === 1) return <Navigate to={judgmentPath(caseId, view.library[0].judgment_id)} replace />
  const outside = view !== null && view.library.length === 0
  return (
    <AppShell caseId={caseId}>
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Link to={`/cases/${caseId}?tab=search`} className="text-sm text-blue-700 underline">回判決檢索</Link>
        {error && <p className="text-sm text-red-700">{error}</p>}
        {view && (
          <>
            <h1 className="text-xl font-semibold text-slate-900">{outside ? `${view.display}（不在判決庫）` : view.display}</h1>
            {!outside && (
              <section aria-label="判決庫裡的這個字號" className={`${CARD} space-y-1`}>
                <p className="text-sm text-slate-600">判決庫裡有幾份同字號的裁判，請自己選要看哪一份：</p>
                <ul className="space-y-1 text-sm">
                  {view.library.map((l) => (
                    <li key={l.judgment_id}>
                      <Link to={judgmentPath(caseId, l.judgment_id)} className="text-blue-700 underline">{l.citation}</Link>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {outside && (
              <p className="text-xs text-slate-500">
                {isUnknownCourt(view.key) ? `${UNKNOWN_COURT}：下面是寫了這個字號的判決，可能是不同法院的不同判決` : importHint(view.key, view.supreme_importable)}
              </p>
            )}
            <section aria-label="引用它的判決" className={`${CARD} space-y-2`}>
              <h2 className="text-lg font-semibold text-slate-800">{`庫裡有 ${view.citing.length} 份判決引用`}</h2>
              <ul className="space-y-3 text-sm">
                {view.citing.map((c) => (
                  <li key={c.judgment_id}>
                    <Link to={judgmentPath(caseId, c.judgment_id)} className="text-blue-700 underline">{c.citation}</Link>
                    <p className="text-slate-700">{c.sentence}</p>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
    </AppShell>
  )
}

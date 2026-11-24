import { useCallback, useEffect, useState } from 'react'
import * as api from '../api'
import { busyText, POLL_MS, useJobPolling } from '../aiJobs'
import { BTN_PRIMARY } from '../ui'
import SourceBadge from './SourceBadge'

const LEVELS: { level: api.HealthItemView['level']; label: string; className: string }[] = [
  { level: 'high', label: '高風險', className: 'bg-red-50 text-red-700' },
  { level: 'suggest', label: '補強建議', className: 'bg-amber-50 text-amber-700' },
  { level: 'gap', label: '證據缺口', className: 'bg-slate-100 text-slate-600' },
]

function HealthItem({ item, onShowSource }: { item: api.HealthItemView; onShowSource?: (s: api.SourceView) => void }) {
  return (
    <li className="space-y-1 py-2">
      <p className="font-medium text-slate-900">{item.title}</p>
      {item.explanation && <p className="text-sm text-slate-700">{item.explanation}</p>}
      {item.claim_seqs.length > 0 && <p className="text-xs text-slate-500">相關：第 {item.claim_seqs.join('、')} 項</p>}
      {item.absence && <p className="text-xs text-slate-400">缺漏類，系統無法比對，請律師判斷</p>}
      {item.basis.map((b, i) => (
        <div key={`${b.page_id}-${i}`}><SourceBadge source={b} origin="ai" lawyerLabel="律師" onShow={onShowSource} /></div>
      ))}
      {item.references.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {item.references.map((r) => (
            <span key={r.code} className="rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-xs text-indigo-800">
              {r.label}
            </span>
          ))}
        </div>
      )}
      {item.not_in_list.map((t) => <p key={t} className="text-xs text-red-700">不在清單：{t}</p>)}
    </li>
  )
}

// 書狀健檢與爭點整理表（設計 5.8）：起草完成後執行；只給律師參考，不進書狀
export default function HealthCheckPanel({ roundId, briefKind, pollMs = POLL_MS, onShowSource }: {
  roundId: string
  briefKind: api.BriefKind
  pollMs?: number
  onShowSource?: (s: api.SourceView) => void
}) {
  const [report, setReport] = useState<api.HealthReportView | null>(null)
  const [job, setJob] = useState<api.JobView | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const body = await api.getHealthReport(roundId)
      setReport(body.report)
      setJob(body.job)
    } catch (e) {
      setMessage((e as Error).message)
    }
  }, [roundId])

  useEffect(() => { void load() }, [load])
  const running = job?.status === 'running' && !job.stale
  useJobPolling(running, load, pollMs)

  async function run() {
    setMessage(null)
    try {
      setJob((await api.runHealthCheck(roundId)).job)
    } catch (e) {
      setMessage((e as Error).message)
    }
    await load()
  }

  return (
    <section className="mt-3 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={run} disabled={running}
          className={BTN_PRIMARY}>
          {report ? '重新執行健檢' : '執行健檢'}
        </button>
        <h3 className="font-semibold text-slate-800">書狀健檢與爭點整理</h3>
        {running && <span className="text-sm text-amber-700">{busyText('health')}</span>}
      </div>
      {report?.outdated && <p className="rounded bg-yellow-50 p-2 text-sm text-yellow-800">資料已變更，建議重新執行</p>}
      {job?.status === 'failed' && job.error && <p className="text-sm text-red-700">{job.error}</p>}
      {message && <p className="text-sm text-red-700">{message}</p>}
      {report && (
        <>
          <p className="text-xs text-slate-500">健檢是 AI 的評估，只供參考，不會寫進書狀；依據請對照原文出處。</p>
          {report.pending_ocr.map((p) => <p key={p} className="text-xs text-amber-700">{p}待辨識，未處理</p>)}
          {LEVELS.map(({ level, label, className }) => {
            const items = report.items.filter((i) => i.level === level)
            if (!items.length) return null
            return (
              <div key={level}>
                <span className={`rounded px-2 py-0.5 text-sm font-semibold ${className}`}>{label}</span>
                <ul className="divide-y divide-slate-200">
                  {items.map((i, n) => <HealthItem key={`${level}-${n}`} item={i} onShowSource={onShowSource} />)}
                </ul>
              </div>
            )
          })}
          {report.items.length === 0 && <p className="text-sm text-slate-500">沒有發現需要提醒的項目</p>}
          <div className="space-y-1 border-t border-slate-200 pt-2">
            <h4 className="font-semibold text-slate-800">兩造不爭執事項</h4>
            {report.undisputed === null || briefKind === 'complaint' ? (
              <p className="text-sm text-slate-500">起訴時尚無對造陳述</p>
            ) : report.undisputed.length === 0 ? <p className="text-sm text-slate-500">無</p> : (
              <ul className="space-y-1">
                {report.undisputed.map((u, i) => (
                  <li key={i} className="text-sm text-slate-800">
                    {u.fact}
                    <div><SourceBadge source={u.basis} origin="ai" lawyerLabel="律師" onShow={onShowSource} /></div>
                  </li>
                ))}
              </ul>
            )}
            <h4 className="font-semibold text-slate-800">{report.issues_label}</h4>
            {report.issues.length === 0 ? <p className="text-sm text-slate-500">無</p> : (
              <ol className="list-decimal space-y-0.5 pl-5 text-sm text-slate-800">
                {report.issues.map((t) => <li key={t}>{t}</li>)}
              </ol>
            )}
          </div>
        </>
      )}
    </section>
  )
}

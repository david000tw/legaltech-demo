import { useState } from 'react'
import type * as api from '../api'
import { KIND_LABEL } from '../labels'
import HealthCheckPanel from './HealthCheckPanel'

// 「書狀健檢」分頁：選一個已起草、或已有健檢結果的輪次（預設最新一輪），顯示該輪的健檢與爭點整理
// 健檢在起草階段裡、起草之前跑（設計 6.3）：起草失敗時也要看得到健檢結果與「重新執行」
export default function HealthTab({ rounds, onShowSource }: {
  rounds: api.RoundSummary[]
  onShowSource?: (s: api.SourceView) => void
}) {
  const drafted = rounds.filter((r) => r.status === 'drafted' || r.has_health).sort((a, b) => a.seq - b.seq)
  const [chosen, setChosen] = useState<string | null>(null)
  const current = drafted.find((r) => r.id === chosen) ?? drafted[drafted.length - 1]
  if (!current) return <p className="text-sm text-slate-500">起草完成後才能執行健檢。</p>
  const kind = (r: api.RoundSummary) => r.brief_kind ?? 'preparatory'
  return (
    <div className="space-y-2">
      <label className="text-sm text-slate-700">
        健檢的輪次
        <select aria-label="健檢的輪次" value={current.id} onChange={(e) => setChosen(e.target.value)}
          className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
          {drafted.map((r) => <option key={r.id} value={r.id}>{`第${r.seq}輪・${KIND_LABEL[kind(r)]}`}</option>)}
        </select>
      </label>
      <HealthCheckPanel key={current.id} roundId={current.id} briefKind={kind(current)} onShowSource={onShowSource} />
    </div>
  )
}

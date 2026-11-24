import { useState } from 'react'
import * as api from '../api'

// 支持度檢查（計畫 2B，設計 7.3）：每個引用旁的紅黃標籤、說明、放行與重新檢查。
const TONE = {
  gray: 'bg-slate-100 text-slate-700',
  yellow: 'bg-amber-100 text-amber-800',
  red: 'bg-red-100 text-red-800',
} as const

function Tag({ tone, children }: { tone: keyof typeof TONE; children: string }) {
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${TONE[tone]}`}>{children}</span>
}

function releasedText(s: api.SupportView): string {
  const r = s.released!
  const who = `${r.name ?? ''}（${r.bar_number ?? ''}）${r.at.slice(0, 10)}`
  const head = s.status === 'failed' ? '已放行（未經 AI 檢查）' : '已放行'
  return `${head}：${who}${r.reason ? `：${r.reason}` : ''}`
}

export default function SupportBadge({ citation, draftId, canReview, onAction }: {
  citation: api.CitationView
  draftId: string
  canReview: boolean
  onAction: (f: () => Promise<unknown>) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const s = citation.support
  if (!s) return null
  const code = <span className="mr-2 text-slate-600">{citation.code}</span>
  if (s.released) {
    return <p className="text-xs text-slate-500">{code}<span>{releasedText(s)}</span></p>
  }
  if (s.status === 'pending') {
    return <p className="text-xs">{code}<Tag tone="gray">支持度檢查中</Tag></p>
  }
  if (s.status === 'done' && s.verdict === 'supports') return null
  if (s.verdict === 'party_claim_misuse' && citation.court_confirmed) {
    return (
      <p className="text-xs text-slate-500">
        {code}<span>{`AI 認為可能是當事人主張（已確認為法院見解）：${s.explanation ?? ''}`}</span>
      </p>
    )
  }

  const failed = s.status === 'failed'
  const partial = s.verdict === 'partial'
  const needsReason = !partial  // 紅色與尚未檢查都要填理由
  const tag = failed ? <Tag tone="yellow">支持度尚未檢查</Tag>
    : partial ? <Tag tone="yellow">部分支持</Tag>
      : s.verdict === 'not_supported' ? <Tag tone="red">不支持</Tag>
        : <Tag tone="red">誤用當事人主張</Tag>

  const send = () => onAction(async () => {
    await api.releaseSupport(citation.id, reason.trim())
    setOpen(false)
    setReason('')
  })

  return (
    <div className="space-y-1 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {code}
        {tag}
        {!failed && s.explanation && <span className="text-slate-700">{s.explanation}</span>}
        {failed && (
          <button type="button" onClick={() => onAction(() => api.retrySupport(draftId))}
            className="rounded border px-2 py-0.5">重新檢查</button>
        )}
        {canReview && !open && (
          <button type="button" onClick={() => setOpen(true)}
            className="rounded border border-amber-400 px-2 py-0.5 text-amber-800">
            {partial ? '確認放行' : '填理由放行'}
          </button>
        )}
      </div>
      {canReview && open && (
        <div className="flex flex-wrap items-center gap-2">
          <input aria-label="放行理由" value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder={needsReason ? '請填寫放行理由' : '理由（可不填）'}
            className="min-w-48 flex-1 rounded border border-slate-300 px-2 py-1" />
          <button type="button" disabled={needsReason && !reason.trim()} onClick={() => void send()}
            className="rounded bg-amber-600 px-2 py-1 text-white disabled:opacity-50">送出</button>
          <button type="button" onClick={() => { setOpen(false); setReason('') }}
            className="rounded border px-2 py-1">取消</button>
        </div>
      )}
    </div>
  )
}

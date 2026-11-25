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

// 放行日期以台灣時間顯示（後端存 UTC；台灣凌晨放行時 UTC 還是前一天）
const TAIPEI_DATE = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei' })

function releasedText(s: api.SupportView): string {
  const r = s.released!
  const who = `${r.name ?? ''}（${r.bar_number ?? ''}）${TAIPEI_DATE.format(new Date(r.at))}`
  const head = s.status === 'failed' ? '已放行（未經 AI 檢查）' : '已放行'
  return `${head}：${who}${r.reason ? `：${r.reason}` : ''}`
}

// 「支持度尚未檢查」的原因（後端 error 記成「種類: 訊息」）：額度用完時重查沒用，律師該改用放行
function uncheckedReason(error: string | null): string | null {
  if (!error) return null
  if (error.startsWith('usage_limit')) return '原因：Claude 用量額度已用完，額度恢復後再重新檢查，或填理由放行'
  if (error.startsWith('not_logged_in')) return '原因：本機 Claude Code 尚未登入'
  if (error.startsWith('timeout')) return '原因：AI 逾時，可以重新檢查'
  if (error.startsWith('invalid_output')) return '原因：AI 回傳不完整，可以重新檢查'
  return `原因：${error.replace(/^[a-z_]+: /, '')}`
}

export default function SupportBadge({ citation, draftId, canReview, busy = false, onAction }: {
  citation: api.CitationView
  draftId: string
  canReview: boolean
  busy?: boolean  // 同一段有操作在進行：按鈕停用，避免連按送出兩次
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
    await api.releaseSupport(citation.id, reason.trim(), s.version ?? '')  // 帶回律師看到的那一版
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
          <button type="button" disabled={busy} onClick={() => onAction(() => api.retrySupport(draftId))}
            className="rounded border px-2 py-0.5 disabled:opacity-50">重新檢查</button>
        )}
        {partial && <span className="text-amber-800">按「已審」即接受</span>}
        {canReview && !open && !partial && (
          <button type="button" disabled={busy} onClick={() => setOpen(true)}
            className="rounded border border-amber-400 px-2 py-0.5 text-amber-800 disabled:opacity-50">
            填理由放行
          </button>
        )}
      </div>
      {failed && uncheckedReason(s.error) && <p className="text-slate-600">{uncheckedReason(s.error)}</p>}
      {canReview && open && (
        <div className="flex flex-wrap items-center gap-2">
          <input aria-label="放行理由" value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder={needsReason ? '請填寫放行理由' : '理由（可不填）'}
            className="min-w-48 flex-1 rounded border border-slate-300 px-2 py-1" />
          <button type="button" disabled={busy || (needsReason && !reason.trim())} onClick={() => void send()}
            className="rounded bg-amber-600 px-2 py-1 text-white disabled:opacity-50">送出</button>
          <button type="button" onClick={() => { setOpen(false); setReason('') }}
            className="rounded border px-2 py-1">取消</button>
        </div>
      )}
    </div>
  )
}

import type { SourceView } from '../api'

// 新步驟的原文出處標示（設計 5.1）。畫面不得出現「已驗證」「已證實」。
export const SOURCE_REASON_LABEL: Record<string, string> = {
  quote_not_found: '未找到原文',
  date_not_in_quote: '日期不在原文摘錄裡',
  amount_not_in_quote: '金額不在原文摘錄裡',
  date_invalid: '日期無法判讀',
  not_opponent: '不是本輪對造書狀的內容',
}

export const reasonsText = (reasons: string[]) =>
  reasons.length ? reasons.map((r) => SOURCE_REASON_LABEL[r] ?? r).join('、') : '未找到原文'

export default function SourceBadge({ source, origin, lawyerLabel, onShow }: {
  source: SourceView | null
  origin: 'ai' | 'lawyer'
  lawyerLabel: '律師修改' | '律師'
  onShow?: (s: SourceView) => void
}) {
  // 律師寫的或改過的：不比對原文，只標「律師」並列出處位置（不寫「原文出處」）
  if (origin === 'lawyer') {
    return (
      <span className="inline-flex flex-wrap items-center gap-1 text-xs">
        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700">{lawyerLabel}</span>
        {source?.label && <span className="text-slate-500">出處：{source.label}</span>}
      </span>
    )
  }
  if (!source) return null
  if (source.status === 'absence') {
    return <span className="text-xs text-slate-400">缺漏類，系統無法比對，請律師判斷</span>
  }
  if (source.status === 'found') {
    const text = `原文出處：${source.label ?? ''}`
    return (
      <span className="inline-flex flex-wrap items-center gap-1 text-xs">
        {onShow ? (
          <button type="button" onClick={() => onShow(source)} className="text-slate-700 underline">{text}</button>
        ) : <span className="text-slate-700">{text}</span>}
        {source.quote && <span className="text-slate-500">「{source.quote}」</span>}
      </span>
    )
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1 text-xs">
      <span className="text-red-700">{reasonsText(source.reasons)}</span>
      {source.label && <span className="text-slate-500">（{source.label}）</span>}
    </span>
  )
}

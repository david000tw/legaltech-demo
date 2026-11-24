import { pageImageUrl, type CitationSource, type CitationView } from '../api'
import { OCR_TAG } from './SourceBadge'

export default function SourcePane({ source, citation, canReview = false, onConfirmCourt, onVerify }: {
  source: CitationSource | null
  citation?: CitationView | null
  canReview?: boolean
  onConfirmCourt?: (c: CitationView) => void
  onVerify?: (s: CitationSource) => void  // 律師對照原圖後勾「已對照原圖」（計畫 2A）
}) {
  if (!source) {
    return <div data-testid="source-pane" className="p-4 text-sm text-slate-500">點選引用，這裡會顯示原文並標出被引用的段落。</div>
  }
  const [s, e] = source.span ?? [0, 0]
  // 勾「已對照原圖」會解除整頁：先列出這一頁的提醒，紅色排前面
  const alerts = [...(source.alerts ?? [])].sort((a, b) => (a.color === 'red' ? 0 : 1) - (b.color === 'red' ? 0 : 1))
  return (
    <div data-testid="source-pane" className="space-y-2 p-4">
      <h3 className="font-semibold text-slate-800">{source.title}</h3>
      {source.needs_check && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">{OCR_TAG}</span>
          {canReview && onVerify && (
            <button type="button" onClick={() => onVerify(source)}
              className="rounded border border-amber-400 bg-white px-2 py-0.5 text-amber-900">已對照原圖</button>
          )}
        </div>
      )}
      {source.needs_check && alerts.length > 0 && (
        <div className="space-y-1 text-xs">
          <p className="text-slate-700">{`這一頁有 ${alerts.length} 則提醒，請對照原圖後再勾選：`}</p>
          <ul className="space-y-1">
            {alerts.map((a, i) => (
              <li key={`${a.kind}-${a.start}-${i}`}
                className={`rounded border px-2 py-0.5 ${a.color === 'red'
                  ? 'border-red-300 bg-red-50 text-red-800' : 'border-amber-300 bg-amber-50 text-amber-800'}`}>
                {a.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {source.has_image && source.page_id && (
        <img src={pageImageUrl(source.page_id)} alt={`${source.title} 原圖`}
          className="w-full rounded border border-slate-200" />
      )}
      {source.annotation && (
        <p className="rounded border border-purple-300 bg-purple-50 p-2 text-xs text-purple-900">{source.annotation}</p>
      )}
      {!source.span && <p className="text-xs text-red-700">引文在原文中找不到。</p>}
      {citation?.verify_status === 'warn' && !citation.court_confirmed && (
        <div className="space-y-1 rounded border border-orange-300 bg-yellow-50 p-2 text-xs text-orange-900">
          <p>這句引文的寫法像是法院轉述的當事人主張。請讀完下方原文，確定它是法院自己的判斷再確認。</p>
          {canReview && onConfirmCourt && (
            <button type="button" onClick={() => onConfirmCourt(citation)}
              className="rounded border border-orange-400 bg-white px-2 py-0.5">已讀原文，此句為法院判斷</button>
          )}
        </div>
      )}
      <p className="whitespace-pre-wrap text-sm leading-7 text-slate-800">
        {source.span ? (
          <>
            {source.text.slice(0, s)}
            <mark className="bg-yellow-200">{source.text.slice(s, e)}</mark>
            {source.text.slice(e)}
          </>
        ) : source.text}
      </p>
    </div>
  )
}

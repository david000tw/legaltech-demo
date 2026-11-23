import type { CitationSource, CitationView } from '../api'

export default function SourcePane({ source, citation, canReview = false, onConfirmCourt }: {
  source: CitationSource | null
  citation?: CitationView | null
  canReview?: boolean
  onConfirmCourt?: (c: CitationView) => void
}) {
  if (!source) {
    return <div data-testid="source-pane" className="p-4 text-sm text-slate-500">點選引用，這裡會顯示原文並標出被引用的段落。</div>
  }
  const [s, e] = source.span ?? [0, 0]
  return (
    <div data-testid="source-pane" className="space-y-2 p-4">
      <h3 className="font-semibold text-slate-800">{source.title}</h3>
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

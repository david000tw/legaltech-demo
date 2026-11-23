import type { CitationSource } from '../api'

export default function SourcePane({ source }: { source: CitationSource | null }) {
  if (!source) {
    return <div data-testid="source-pane" className="p-4 text-sm text-slate-500">點選引用，這裡會顯示原文並標出被引用的段落。</div>
  }
  const [s, e] = source.span ?? [0, 0]
  return (
    <div data-testid="source-pane" className="space-y-2 p-4">
      <h3 className="font-semibold text-slate-800">{source.title}</h3>
      {!source.span && <p className="text-xs text-red-700">引文在原文中找不到。</p>}
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

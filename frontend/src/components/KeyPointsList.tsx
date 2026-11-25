import type * as api from '../api'

// 草稿頁最上方的重點清單（設計 6.5）：這份草稿針對對方哪幾點寫了回應、寫在第幾段。
// 對應由 AI 標註；規則只抓「已勾選卻沒有段落回應」，所以標題註明「對應由 AI 標註」。
const DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']

function cnNumber(n: number): string {
  if (n < 10) return DIGITS[n]
  if (n < 20) return `十${n % 10 ? DIGITS[n % 10] : ''}`
  if (n < 100) return `${DIGITS[Math.floor(n / 10)]}十${n % 10 ? DIGITS[n % 10] : ''}`
  return String(n)
}

export default function KeyPointsList({ data, onJump }: { data: api.KeyPoints; onJump: (paragraphSeq: number) => void }) {
  const answered = data.items.filter((it) => it.selected)
  const ordered = [...answered, ...data.items.filter((it) => !it.selected)]  // 沒勾選的排最後
  const title = data.complaint
    ? '這份草稿涵蓋的請求項目（對應由 AI 標註）'
    : `這份草稿回應了對方 ${answered.length} 個重點（對應由 AI 標註）`
  if (data.items.length === 0) return null
  return (
    <section aria-label="重點清單" className="space-y-1 rounded-lg border border-slate-200 bg-white p-3 text-sm">
      <h2 className="font-semibold text-slate-900">{title}</h2>
      {data.legacy && <p className="text-xs text-slate-500">這份草稿是舊版產生的，沒有對應資料</p>}
      <ol className="space-y-1">
        {ordered.map((it) => (
          <li key={it.seq} className={`flex flex-wrap items-baseline gap-x-2 ${it.selected ? '' : 'text-slate-400'}`}>
            <span className="tabular-nums text-slate-500">{it.seq}</span>
            <span>{it.summary}</span>
            {!it.selected && <span className="text-xs">未勾選，不回應</span>}
            {it.paragraphs.map((p) => (
              <button key={p.seq} type="button" onClick={() => onJump(p.seq)}
                className="text-left text-sky-700 hover:underline">
                {`第${cnNumber(p.no)}段：${p.heading}`}
              </button>
            ))}
            {it.uncovered && !data.legacy && <span className="text-xs text-amber-700">⚠ 沒有段落回應</span>}
          </li>
        ))}
      </ol>
    </section>
  )
}

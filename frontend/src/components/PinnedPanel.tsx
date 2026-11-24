import { useCallback, useEffect, useState, type MouseEvent } from 'react'
import * as api from '../api'
import { BTN_SECONDARY } from '../ui'

export type ParagraphChoice = { id: string; no: number; heading: string }
const MIN_QUOTE = 15

// 一則釘選：在全文裡選取文字當引文，選一個內文段落，插入到該段結尾
function PinCard({ pin, paragraphs, onInserted }: {
  pin: api.PassageView; paragraphs: ParagraphChoice[]; onInserted: () => void
}) {
  const [target, setTarget] = useState(paragraphs[0]?.id ?? '')
  const [quote, setQuote] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const chosen = paragraphs.find((p) => p.id === target) ?? paragraphs[0]

  // 只接受在這一段全文之內選取的文字
  function capture(e: MouseEvent<HTMLElement>) {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) return
    const el = e.currentTarget
    if (!el.contains(sel.anchorNode) || !el.contains(sel.focusNode)) return
    const text = sel.toString().trim()
    if (text) setQuote(text)
  }

  async function insert() {
    if (!chosen) return
    setBusy(true)
    setMessage(null)
    try {
      await api.insertPin(chosen.id, pin.pin_id!, quote.trim())
      setQuote('')
      onInserted()
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="space-y-2 px-3 py-3 text-sm">
      <p className="font-medium text-slate-900">{pin.citation}</p>
      {pin.note && <p className="text-xs text-amber-800">{pin.note}</p>}
      {pin.annotation && <p className="rounded bg-purple-50 p-1 text-xs text-purple-900">{pin.annotation}</p>}
      <p onMouseUp={capture} className="whitespace-pre-wrap leading-7 text-slate-800">{pin.text}</p>
      <p className="text-xs text-slate-500">在上方全文中選取要引用的原文（至少 15 字），會自動填入引文欄。</p>
      <textarea aria-label="引文（必填，至少 15 字）" value={quote} onChange={(e) => setQuote(e.target.value)} rows={2}
        placeholder="引文（必填，至少 15 字）" className="w-full rounded border border-slate-300 p-2" />
      {paragraphs.length === 0 ? <p className="text-xs text-slate-500">草稿沒有內文段落可以插入</p> : (
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="插入位置" value={chosen?.id} onChange={(e) => setTarget(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm">
            {paragraphs.map((p) => <option key={p.id} value={p.id}>{`第 ${p.no} 段 ${p.heading}`}</option>)}
          </select>
          <button type="button" onClick={insert} disabled={busy || quote.trim().length < MIN_QUOTE}
            className={BTN_SECONDARY}>{`插入到第 ${chosen?.no} 段`}</button>
        </div>
      )}
      <p className="text-xs text-slate-500">插入後該段要重新審核，段落裡的判決引用要重新確認。</p>
      {message && <p className="text-xs text-red-700">{message}</p>}
    </li>
  )
}

// 草稿頁左側「已釘選判決」（設計 5.9）：把案件釘選的判決插進指定的內文段落
export default function PinnedPanel({ caseId, paragraphs, onInserted }: {
  caseId: string; paragraphs: ParagraphChoice[]; onInserted: () => void
}) {
  const [pins, setPins] = useState<api.PassageView[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setPins(await api.listPins(caseId))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [caseId])
  useEffect(() => { void load() }, [load])

  if (error) return <p className="p-3 text-sm text-red-700">{error}</p>
  if (!pins) return <p className="p-3 text-sm text-slate-500">載入中…</p>
  if (pins.length === 0) {
    return <p className="p-3 text-sm text-slate-500">還沒有釘選的判決：請到案件頁的「判決檢索」分頁搜尋並釘選。</p>
  }
  return (
    <ul className="divide-y divide-slate-100">
      {pins.map((p) => <PinCard key={p.pin_id} pin={p} paragraphs={paragraphs} onInserted={onInserted} />)}
    </ul>
  )
}

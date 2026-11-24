import { useCallback, useEffect, useState, type FormEvent } from 'react'
import * as api from '../api'
import { highlight } from '../highlight'
import { BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL, CARD } from '../ui'

// 一段法院判斷：字號、法院、日期、少數見解註記、全文（查詢字詞反白），以及釘選狀態
function PassageCard({ p, terms, pinned, onPin }: {
  p: api.PassageView; terms: string[]; pinned: boolean; onPin: (p: api.PassageView) => void
}) {
  return (
    <li className={CARD}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium text-slate-900">{p.citation}</p>
          <p className="text-xs text-slate-500">{p.court}・{p.decided_on}・法院判斷段</p>
        </div>
        {pinned
          ? <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">已釘選</span>
          : <button type="button" onClick={() => onPin(p)} className={BTN_SMALL}>釘選</button>}
      </div>
      {p.annotation && (
        <p className="mt-2 rounded border border-purple-300 bg-purple-50 p-2 text-xs text-purple-900">{p.annotation}</p>
      )}
      <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-800">{highlight(p.text, terms)}</p>
    </li>
  )
}

function PinItem({ p, onChanged, onError }: {
  p: api.PassageView; onChanged: () => Promise<void>; onError: (m: string) => void
}) {
  const [note, setNote] = useState(p.note)
  async function run(fn: () => Promise<unknown>) {
    try {
      await fn()
      await onChanged()
    } catch (e) {
      onError((e as Error).message)
    }
  }
  return (
    <li className="space-y-1 px-3 py-2 text-sm">
      <p className="font-medium text-slate-900">{p.citation}</p>
      <p className="line-clamp-3 text-xs text-slate-600">{p.text}</p>
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="備註" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
          placeholder="備註（例如：用在哪個爭點）" className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-0.5" />
        <button type="button" onClick={() => run(() => api.patchPin(p.pin_id!, note))} className={BTN_SMALL}>儲存備註</button>
        <button type="button" onClick={() => run(() => api.deletePin(p.pin_id!))} className={BTN_SMALL}>取消釘選</button>
      </div>
    </li>
  )
}

// 「判決檢索」分頁（設計 5.9）：搜尋判決庫的法院判斷段、釘選到本案；也可貼司法院網址加入判決庫
export default function JudgmentSearchTab({ caseId }: { caseId: string }) {
  const [q, setQ] = useState('')
  const [scope, setScope] = useState<'case_type' | 'all'>('case_type')
  const [results, setResults] = useState<api.PassageView[] | null>(null)
  const [terms, setTerms] = useState<string[]>([])
  const [pins, setPins] = useState<api.PassageView[]>([])
  const [url, setUrl] = useState('')
  const [imported, setImported] = useState<api.ImportedJudgment | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const loadPins = useCallback(async () => {
    try {
      setPins(await api.listPins(caseId))
    } catch (e) {
      setMessage((e as Error).message)
    }
  }, [caseId])
  useEffect(() => { void loadPins() }, [loadPins])
  const pinned = new Set(pins.map((p) => p.passage_id))

  async function act(fn: () => Promise<void>) {
    setBusy(true)
    setMessage(null)
    try {
      await fn()
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const search = (e: FormEvent) => {
    e.preventDefault()
    return act(async () => {
      const body = await api.searchJudgments(caseId, q, scope)
      setResults(body.results)
      setTerms(body.terms)
    })
  }
  const pin = (p: api.PassageView) => act(async () => {
    await api.pinPassage(caseId, p.passage_id, '')
    await loadPins()
  })
  const importUrl = (e: FormEvent) => {
    e.preventDefault()
    return act(async () => {
      setImported(null)
      setImported(await api.importJudgment(caseId, url.trim()))
    })
  }

  return (
    <div className="space-y-6">
      <form onSubmit={search} className="flex flex-wrap items-end gap-2">
        <label className="text-sm text-slate-700">
          查詢字詞
          <input aria-label="查詢字詞" value={q} onChange={(e) => setQ(e.target.value)} placeholder="例如：慰撫金 鎖骨骨折"
            className="ml-2 w-72 rounded border border-slate-300 px-2 py-1.5" />
        </label>
        <select aria-label="搜尋範圍" value={scope} onChange={(e) => setScope(e.target.value as 'case_type' | 'all')}
          className="rounded border border-slate-300 px-2 py-1.5 text-sm">
          <option value="case_type">本案案型</option>
          <option value="all">全部</option>
        </select>
        <button type="submit" disabled={busy || !q.trim()} className={BTN_PRIMARY}>搜尋</button>
      </form>
      {message && <p className="text-sm text-red-700">{message}</p>}

      {results && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold text-slate-800">搜尋結果</h2>
          {results.length === 0 && <p className="text-sm text-slate-500">沒有找到相關的法院判斷段</p>}
          <ul className="space-y-3">
            {results.map((p) => (
              <PassageCard key={p.passage_id} p={p} terms={terms} pinned={pinned.has(p.passage_id)} onPin={pin} />
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-lg font-semibold text-slate-800">已釘選的判決</h2>
        <p className="text-xs text-slate-500">之後每一輪起草時，釘選的段落會排在候選判決的最前面；也可以在草稿頁插入到指定段落。</p>
        {pins.length === 0 ? <p className="text-sm text-slate-500">尚未釘選</p> : (
          <ul aria-label="已釘選" className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {pins.map((p) => <PinItem key={`${p.pin_id}-${p.note}`} p={p} onChanged={loadPins} onError={setMessage} />)}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold text-slate-800">加入司法院判決</h2>
        <form onSubmit={importUrl} className="flex flex-wrap items-center gap-2">
          <input aria-label="貼上司法院判決網址" value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://judgment.judicial.gov.tw/…" className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1.5 text-sm" />
          <button type="submit" disabled={busy || !url.trim()} className={BTN_SECONDARY}>加入</button>
        </form>
        <p className="text-xs text-slate-500">只接受司法院判決書系統的民事、家事判決網址；系統會向司法院下載判決，切成段落後加入判決庫。</p>
        {imported && (
          <div className="space-y-2">
            <p className="text-sm text-slate-700">
              {imported.created ? '已加入判決庫：' : '判決庫已有這件判決：'}{imported.judgment.citation}
            </p>
            <ul className="space-y-3">
              {imported.passages.map((p) => (
                <PassageCard key={p.passage_id} p={p} terms={[]} pinned={pinned.has(p.passage_id)} onPin={pin} />
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  )
}

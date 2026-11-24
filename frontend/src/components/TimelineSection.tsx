import { useCallback, useEffect, useState, type FormEvent } from 'react'
import * as api from '../api'
import { BUSY_TEXT, POLL_MS, useJobPolling } from '../aiJobs'
import SourceBadge from './SourceBadge'

const money = (n: number | null) => (n == null ? '' : `新臺幣${n.toLocaleString('en-US')}元`)
export const AMOUNT_HINT = '金額請填數字，例如 45000 或 45,000元'
// 律師常照書狀寫「新臺幣45,000元」：去掉幣別、元、逗號與空白後要是整數；讀不懂就擋下，不默默變成沒有金額
const toAmount = (s: string): number | null => {
  const plain = s.replace(/新臺幣|新台幣|NT\$|元|[,，\s]/g, '')
  if (plain === '') return null
  if (!/^\d+$/.test(plain)) throw new Error(AMOUNT_HINT)
  return Number(plain)
}

function EventRow({ ev, onChanged, onError, onShowSource, compact }: {
  ev: api.TimelineEventView
  onChanged: () => Promise<void>
  onError: (m: string | null) => void
  onShowSource?: (s: api.SourceView) => void
  compact: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [date, setDate] = useState(ev.date ?? '')
  const [description, setDescription] = useState(ev.description)
  const [amount, setAmount] = useState(ev.amount == null ? '' : String(ev.amount))

  async function act(f: () => Promise<unknown>) {
    onError(null)
    try {
      await f()
      setEditing(false)
    } catch (e) {
      onError((e as Error).message)
    }
    await onChanged()
  }

  const save = () => act(() => api.patchTimelineEvent(ev.id, { date: date.trim() || null, description,
    amount: toAmount(amount) }))

  return (
    <tr className="align-top">
      <td className="whitespace-nowrap px-2 py-1">
        {editing ? (
          <input aria-label="事件日期" value={date} onChange={(e) => setDate(e.target.value)} placeholder="2025-03-09"
            className="w-28 rounded border border-slate-300 px-1 py-0.5" />
        ) : (ev.date_label ?? '（無日期）')}
      </td>
      <td className="px-2 py-1">
        {editing ? (
          <input aria-label="事件內容" value={description} onChange={(e) => setDescription(e.target.value)}
            className="w-full rounded border border-slate-300 px-1 py-0.5" />
        ) : (
          <span>
            {ev.source_kind === 'opponent' && (
              <span className="mr-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">對造主張</span>
            )}
            {ev.description}
          </span>
        )}
      </td>
      <td className="whitespace-nowrap px-2 py-1">
        {editing ? (
          <input aria-label="事件金額" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="numeric"
            className="w-24 rounded border border-slate-300 px-1 py-0.5" />
        ) : money(ev.amount)}
      </td>
      <td className="px-2 py-1">
        <div className="space-y-1">
          <SourceBadge source={ev.source} origin={ev.origin} lawyerLabel="律師" onShow={onShowSource} />
          {!compact && (
            <div className="flex gap-2">
              {editing ? (
                <>
                  <button type="button" onClick={save} disabled={!description.trim()}
                    className="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white disabled:opacity-50">儲存</button>
                  <button type="button" onClick={() => setEditing(false)} className="rounded border px-2 py-0.5 text-xs">取消</button>
                </>
              ) : (
                <>
                  <button type="button" onClick={() => setEditing(true)} className="text-xs text-indigo-700 underline">修改</button>
                  <button type="button" onClick={() => act(() => api.deleteTimelineEvent(ev.id))}
                    className="text-xs text-red-700 underline">刪除</button>
                </>
              )}
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}

// 大事記（設計 5.7）：日期｜事件｜金額｜出處；對造書狀來的標「對造主張」。compact＝草稿頁左側，只看不改
export default function TimelineSection({ caseId, pollMs = POLL_MS, compact = false, onShowSource }: {
  caseId: string
  pollMs?: number
  compact?: boolean
  onShowSource?: (s: api.SourceView) => void
}) {
  const [events, setEvents] = useState<api.TimelineEventView[] | null>(null)
  const [pending, setPending] = useState<string[]>([])
  const [job, setJob] = useState<api.JobView | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [date, setDate] = useState('')
  const [description, setDescription] = useState('')
  const [amount, setAmount] = useState('')

  const load = useCallback(async () => {
    try {
      const body = await api.getTimeline(caseId)
      setEvents(body.events)
      setPending(body.pending_ocr)
      setJob(body.job)
    } catch (e) {
      setMessage((e as Error).message)
    }
  }, [caseId])

  useEffect(() => { void load() }, [load])
  const running = job?.status === 'running' && !job.stale
  useJobPolling(running, load, pollMs)

  async function generate() {
    setMessage(null)
    try {
      setJob((await api.generateTimeline(caseId)).job)
    } catch (e) {
      setMessage((e as Error).message)
    }
    await load()
  }

  async function add(e: FormEvent) {
    e.preventDefault()
    setMessage(null)
    try {
      await api.addTimelineEvent(caseId, { date: date.trim() || null, description, amount: toAmount(amount) })
      setDate('')
      setDescription('')
      setAmount('')
    } catch (err) {
      setMessage((err as Error).message)
    }
    await load()
  }

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        {!compact && (
          <button type="button" onClick={generate} disabled={running}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            整理大事記
          </button>
        )}
        <h2 className="text-lg font-semibold text-slate-800">大事記</h2>
        {running && <span className="text-sm text-amber-700">{BUSY_TEXT}</span>}
      </div>
      {job?.status === 'failed' && job.error && <p className="text-sm text-red-700">{job.error}</p>}
      {message && <p className="text-sm text-red-700">{message}</p>}
      {pending.map((p) => <p key={p} className="text-xs text-amber-700">{p}待辨識，未處理</p>)}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-600">
            <tr><th className="px-2 py-1">日期</th><th className="px-2 py-1">事件</th><th className="px-2 py-1">金額</th>
              <th className="px-2 py-1">出處</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {events?.map((ev) => (
              <EventRow key={`${ev.id}-${ev.description}-${ev.date}-${ev.amount}`} ev={ev} onChanged={load}
                onError={setMessage} onShowSource={onShowSource} compact={compact} />
            ))}
          </tbody>
        </table>
        {events?.length === 0 && <p className="px-2 py-2 text-sm text-slate-500">尚無事件</p>}
      </div>
      {!compact && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-2 text-sm">
          <label className="text-slate-700">日期
            <input aria-label="新增事件日期" value={date} onChange={(e) => setDate(e.target.value)} placeholder="2025-03-09"
              className="ml-1 w-28 rounded border border-slate-300 px-1 py-0.5" />
          </label>
          <label className="flex-1 text-slate-700">事件
            <input aria-label="新增事件內容" value={description} onChange={(e) => setDescription(e.target.value)}
              className="ml-1 w-full min-w-40 rounded border border-slate-300 px-1 py-0.5" />
          </label>
          <label className="text-slate-700">金額
            <input aria-label="新增事件金額" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="numeric"
              className="ml-1 w-24 rounded border border-slate-300 px-1 py-0.5" />
          </label>
          <button type="submit" disabled={!description.trim()}
            className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-50 disabled:opacity-50">新增事件</button>
        </form>
      )}
    </section>
  )
}

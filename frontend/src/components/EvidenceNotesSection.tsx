import { useCallback, useEffect, useState } from 'react'
import * as api from '../api'
import { BUSY_TEXT, POLL_MS, useJobPolling } from '../aiJobs'
import SourceBadge from './SourceBadge'

const KIND_TITLE: Record<api.EvidenceNoteView['kind'], string> = { proves: '證明方向', caution: '注意事項' }

function NoteItem({ note, onChanged, onError, onShowSource, compact }: {
  note: api.EvidenceNoteView
  onChanged: () => Promise<void>
  onError: (m: string | null) => void
  onShowSource?: (s: api.SourceView) => void
  compact: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(note.text)

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

  return (
    <li className="space-y-1 py-1">
      {editing ? (
        <div className="space-y-1">
          <textarea aria-label="卷證說明內容" value={text} onChange={(e) => setText(e.target.value)} rows={2}
            className="w-full rounded border border-slate-300 p-2 text-sm" />
          <div className="flex gap-2">
            <button type="button" disabled={!text.trim()} onClick={() => act(() => api.patchEvidenceNote(note.id, text))}
              className="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white disabled:opacity-50">儲存</button>
            <button type="button" onClick={() => { setEditing(false); setText(note.text) }}
              className="rounded border px-2 py-0.5 text-xs">取消</button>
          </div>
        </div>
      ) : <p className="text-sm text-slate-800">{note.text}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <SourceBadge source={note.source} origin={note.origin} lawyerLabel="律師修改" onShow={onShowSource} />
        {!compact && !editing && (
          <>
            <button type="button" onClick={() => setEditing(true)} className="text-xs text-indigo-700 underline">修改</button>
            <button type="button" onClick={() => act(() => api.deleteEvidenceNote(note.id))}
              className="text-xs text-red-700 underline">刪除</button>
          </>
        )}
      </div>
    </li>
  )
}

// 卷證說明（設計 5.6）：每份我方卷證一張卡，列證明方向、注意事項與待辨識頁。compact＝草稿頁左側，只看不改
export default function EvidenceNotesSection({ caseId, pollMs = POLL_MS, compact = false, onShowSource }: {
  caseId: string
  pollMs?: number
  compact?: boolean
  onShowSource?: (s: api.SourceView) => void
}) {
  const [docs, setDocs] = useState<api.EvidenceDocView[] | null>(null)
  const [job, setJob] = useState<api.JobView | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const body = await api.getEvidenceNotes(caseId)
      setDocs(body.documents)
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
      setJob((await api.generateEvidenceNotes(caseId)).job)
    } catch (e) {
      setMessage((e as Error).message)  // 409：AI 處理中，或所有卷證都已有說明
    }
    await load()
  }

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        {!compact && (
          <button type="button" onClick={generate} disabled={running}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            產生卷證說明
          </button>
        )}
        <h2 className="text-lg font-semibold text-slate-800">卷證說明</h2>
        {running && <span className="text-sm text-amber-700">{BUSY_TEXT}</span>}
      </div>
      {job?.status === 'failed' && job.error && <p className="text-sm text-red-700">{job.error}</p>}
      {message && <p className="text-sm text-red-700">{message}</p>}
      <p className="text-xs text-slate-500">AI 整理的卷證說明只供參考，不會寫進書狀；每句請對照原文出處。</p>
      {docs?.length === 0 && <p className="text-sm text-slate-500">尚未上傳卷證</p>}
      {docs?.map((d) => (
        <article key={d.document_id} className="space-y-1 rounded-lg border border-slate-200 bg-white p-3">
          <h3 className="font-medium text-slate-900">{d.exhibit}<span className="ml-2 text-sm text-slate-500">{d.filename}</span></h3>
          {d.pending_ocr.map((p) => <p key={p} className="text-xs text-amber-700">{p}待辨識，未處理</p>)}
          {d.notes.length === 0 && d.needs_notes && <p className="text-sm text-slate-500">尚未產生說明</p>}
          {(['proves', 'caution'] as const).map((kind) => {
            const notes = d.notes.filter((n) => n.kind === kind)
            if (!notes.length) return null
            return (
              <div key={kind}>
                <h4 className="text-sm font-semibold text-slate-700">{KIND_TITLE[kind]}</h4>
                <ul className="divide-y divide-slate-100">
                  {notes.map((n) => (
                    <NoteItem key={`${n.id}-${n.text}`} note={n} onChanged={load} onError={setMessage}
                      onShowSource={onShowSource} compact={compact} />
                  ))}
                </ul>
              </div>
            )
          })}
        </article>
      ))}
    </section>
  )
}

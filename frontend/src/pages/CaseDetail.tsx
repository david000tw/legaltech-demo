import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import * as api from '../api'
import { headerDraftFrom, headerInput, toDrafts, toInputs, type HeaderDraft, type PartyDraft } from '../caseForms'
import CaseHeaderFields from '../components/CaseHeaderFields'
import PartiesEditor from '../components/PartiesEditor'

const POLL_MS = 3000
const STATUS_LABEL: Record<api.RoundStatus, string> = {
  created: '尚未執行', running: '分析與起草中…', drafted: '草稿完成', failed: '執行失敗',
}

function RoundPanel({ caseId, round, parties, onChanged }: {
  caseId: string; round: api.RoundSummary; parties: api.Party[]; onChanged: () => void
}) {
  const [detail, setDetail] = useState<api.RoundDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // 遞狀人：預設第一位對造；選過的人被刪掉時回到預設
  const [chosenFiler, setChosenFiler] = useState<string | null>(null)
  const defaultFiler = parties.find((p) => p.alignment === 'opposing')?.id ?? ''
  const filer = chosenFiler !== null && (chosenFiler === '' || parties.some((p) => p.id === chosenFiler))
    ? chosenFiler : defaultFiler

  const refresh = useCallback(async () => {
    try {
      setDetail(await api.getRound(round.id))
    } catch (e) {
      setMessage((e as Error).message)
    }
  }, [round.id])

  useEffect(() => { void refresh() }, [refresh])

  const status = detail?.status ?? round.status
  useEffect(() => {
    if (status !== 'running') return
    const t = setInterval(() => { void refresh() }, POLL_MS)
    return () => clearInterval(t)
  }, [status, refresh])

  useEffect(() => { if (status === 'drafted' || status === 'failed') onChanged() }, [status]) // eslint-disable-line react-hooks/exhaustive-deps

  async function upload() {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    setBusy(true)
    setMessage(null)
    try {
      await api.uploadDocument(caseId, file, 'opponent_brief', round.id, filer || undefined)
      onChanged()
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function run() {
    setBusy(true)
    setMessage(null)
    try {
      await api.runRound(round.id)
      await refresh()
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const error = detail?.error ?? round.error
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-800">第 {round.seq} 輪</h3>
        <span className="text-sm text-slate-600">{STATUS_LABEL[status]}{detail?.stale ? '（逾時，可重跑）' : ''}</span>
      </div>
      {status === 'failed' && error && <p className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {(status === 'created' || status === 'failed' || detail?.stale) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {parties.length > 0 && (
            <label className="text-sm text-slate-700">
              遞狀人
              <select aria-label="遞狀人" value={filer} onChange={(e) => setChosenFiler(e.target.value)} disabled={busy}
                className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
                <option value="">（未指定）</option>
                {parties.map((p) => <option key={p.id} value={p.id}>{p.role} {p.name}</option>)}
              </select>
            </label>
          )}
          <label className="text-sm text-slate-700">
            上傳對造書狀
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" className="ml-2 text-sm" onChange={upload} disabled={busy} />
          </label>
          <button type="button" onClick={run} disabled={busy}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            開始分析與起草
          </button>
        </div>
      )}
      {status === 'drafted' && detail?.draft_id && (
        <Link to={`/drafts/${detail.draft_id}`} className="mt-3 inline-block text-sm font-medium text-indigo-700 underline">
          開啟草稿
        </Link>
      )}
      {detail && detail.claims.length > 0 && (
        <ol className="mt-3 space-y-1 text-sm text-slate-700">
          {detail.claims.map((c) => (
            <li key={c.seq}>
              主張{c.seq}：{c.summary}
              {!c.quote_verified && <span className="ml-1 text-red-600">（原文對不上）</span>}
              {(c.references ?? []).length > 0 && (
                <ul className="ml-4 list-disc text-xs">
                  {(c.references ?? []).map((r, i) => (
                    <li key={i} className={r.status === 'mapped' ? 'text-slate-600' : 'text-amber-800'}>
                      {REFERENCE_LABEL[r.status]}：{r.text}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
      {message && <p className="mt-2 text-sm text-red-700">{message}</p>}
    </div>
  )
}

const REFERENCE_LABEL: Record<api.ReferenceView['status'], string> = {
  mapped: '系統對回',
  ambiguous_passage: '對到判決但無法確定段落',
  unmatched: '不在候選清單',
}

const dash = (v: string | null) => v || '（未填）'

function HeaderSection({ data, options, onSaved, onError }: {
  data: api.CaseDetail; options: api.CaseOptions | null; onSaved: () => Promise<void>; onError: (m: string) => void
}) {
  const [draft, setDraft] = useState<HeaderDraft | null>(null)
  const h = data.header
  const typeLabel = options?.case_types.find((t) => t.code === data.case_type)?.label ?? data.case_type

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    try {
      await api.patchCase(data.id, { ...headerInput(draft), addressee: draft.addressee.trim() || null })
      setDraft(null)
      await onSaved()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">狀頭</h2>
        {!draft && (
          <button type="button" onClick={() => setDraft(headerDraftFrom(data))}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">修改狀頭</button>
        )}
      </div>
      {data.procedure_warning && (
        <p className="rounded bg-yellow-50 p-2 text-sm text-yellow-800">{data.procedure_warning}</p>
      )}
      {draft ? (
        <form onSubmit={save} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <CaseHeaderFields value={draft} onChange={setDraft} options={options} addresseeDefault={data.addressee_default} />
          <div className="flex gap-2">
            <button type="submit" className="rounded bg-indigo-600 px-3 py-1 text-sm text-white">儲存狀頭</button>
            <button type="button" onClick={() => setDraft(null)} className="rounded border px-3 py-1 text-sm">取消</button>
          </div>
        </form>
      ) : (
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <dt className="text-slate-500">書狀名稱</dt><dd>{data.brief_title}</dd>
          <dt className="text-slate-500">案型</dt><dd>{typeLabel}</dd>
          <dt className="text-slate-500">法院</dt><dd>{dash(h.court)}</dd>
          <dt className="text-slate-500">案號</dt><dd>{dash(h.case_number)}</dd>
          <dt className="text-slate-500">股別</dt><dd>{dash(h.division)}</dd>
          <dt className="text-slate-500">案由</dt><dd>{dash(h.cause)}</dd>
          <dt className="text-slate-500">受文</dt><dd>{h.addressee || `（預設：${data.addressee_default}）`}</dd>
          <dt className="text-slate-500">訴訟代理人</dt><dd>{dash(h.attorneys.join('、'))}</dd>
          <dt className="text-slate-500">{data.reference_date_label}</dt><dd>{dash(h.reference_date)}</dd>
        </dl>
      )}
    </section>
  )
}

function PartiesSection({ data, options, onSaved, onError }: {
  data: api.CaseDetail; options: api.CaseOptions | null; onSaved: () => Promise<void>; onError: (m: string) => void
}) {
  const [draft, setDraft] = useState<PartyDraft[] | null>(null)
  const alignLabel = (a: api.Alignment) => options?.alignments.find((x) => x.code === a)?.label ?? a

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    try {
      await api.putParties(data.id, toInputs(draft))
      setDraft(null)
      await onSaved()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">當事人</h2>
        {!draft && (
          <button type="button" onClick={() => setDraft(toDrafts(data.parties))}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">修改當事人</button>
        )}
      </div>
      {draft ? (
        <form onSubmit={save} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <PartiesEditor value={draft} onChange={setDraft} options={options} />
          <p className="text-xs text-slate-500">刪除的當事人若是某份對造書狀的遞狀人，該書狀的遞狀人會變成未指定。</p>
          <div className="flex gap-2">
            <button type="submit" className="rounded bg-indigo-600 px-3 py-1 text-sm text-white">儲存當事人</button>
            <button type="button" onClick={() => setDraft(null)} className="rounded border px-3 py-1 text-sm">取消</button>
          </div>
        </form>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {data.parties.map((p) => (
            <li key={p.id} aria-label={`當事人${p.seq}`} className="px-4 py-2 text-sm">
              {p.role} {p.name}（{alignLabel(p.alignment)}）
              {p.legal_rep && <span className="ml-2 text-slate-600">法定代理人 {p.legal_rep}</span>}
              {p.position_note && <span className="ml-2 text-slate-600">{p.position_note}</span>}
            </li>
          ))}
          {data.parties.length === 0 && <li className="px-4 py-2 text-sm text-slate-500">尚未填寫當事人</li>}
        </ul>
      )}
    </section>
  )
}

function ExhibitRow({ exhibit, scanned, onSaved, onError }: {
  exhibit: api.Exhibit
  scanned: boolean
  onSaved: () => Promise<void>
  onError: (m: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [num, setNum] = useState(String(exhibit.number))

  async function save() {
    try {
      await api.renumberExhibit(exhibit.id, Number(num))
      setEditing(false)
      await onSaved()
    } catch (e) {
      onError((e as Error).message)
    }
  }

  return (
    <li className="flex items-center gap-3 px-4 py-2 text-sm">
      <span className="font-medium text-slate-900">{exhibit.label}</span>
      <span className="text-slate-600">{exhibit.filename}</span>
      {scanned && <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">待辨識</span>}
      <span className="ml-auto flex items-center gap-2">
        {editing ? (
          <>
            <input type="number" min={1} value={num} onChange={(e) => setNum(e.target.value)}
              aria-label={`${exhibit.label} 新號碼`} className="w-16 rounded border border-slate-300 px-1 py-0.5" />
            <button type="button" onClick={save} disabled={!(Number(num) >= 1)}
              className="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white disabled:opacity-50">儲存號碼</button>
            <button type="button" onClick={() => { setEditing(false); setNum(String(exhibit.number)) }}
              className="rounded border px-2 py-0.5 text-xs">取消</button>
          </>
        ) : (
          <button type="button" onClick={() => setEditing(true)} aria-label={`改號 ${exhibit.label}`}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700">改號</button>
        )}
      </span>
    </li>
  )
}

export default function CaseDetail() {
  const { caseId = '' } = useParams()
  const notice = (useLocation().state as { notice?: string } | null)?.notice
  const [data, setData] = useState<api.CaseDetail | null>(null)
  const [options, setOptions] = useState<api.CaseOptions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const evidenceRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.getCase(caseId))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [caseId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { api.getCaseOptions().then(setOptions).catch((e: Error) => setError(e.message)) }, [])

  async function uploadEvidence() {
    const file = evidenceRef.current?.files?.[0]
    if (!file) return
    try {
      await api.uploadDocument(caseId, file, 'evidence')
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      if (evidenceRef.current) evidenceRef.current.value = ''
    }
  }

  async function newRound() {
    try {
      await api.createRound(caseId)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!data) return <p className="p-6 text-slate-600">{error ?? '載入中…'}</p>
  const docs = new Map(data.documents.map((d) => [d.id, d]))

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <Link to="/cases" className="text-sm text-indigo-700 underline">← 案件列表</Link>
        <h1 className="mt-2 text-2xl font-bold text-slate-900">{data.title}</h1>
        <p className="text-sm text-slate-600">我方：{data.our_side === 'plaintiff' ? '原告' : '被告'}</p>
        {notice && <p className="mt-2 rounded bg-yellow-50 p-2 text-sm text-yellow-800">{notice}</p>}
      </div>

      <HeaderSection data={data} options={options} onSaved={load} onError={setError} />
      <PartiesSection data={data} options={options} onSaved={load} onError={setError} />

      <section>
        <h2 className="text-lg font-semibold text-slate-800">卷證</h2>
        <ul className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {data.exhibits.map((e) => (
            <ExhibitRow key={`${e.id}-${e.number}`} exhibit={e} onSaved={load} onError={setError}
              scanned={!!docs.get(e.document_id)?.pages.some((p) => p.needs_ocr)} />
          ))}
          {data.exhibits.length === 0 && <li className="px-4 py-2 text-sm text-slate-500">尚未上傳卷證</li>}
        </ul>
        <label className="mt-2 block text-sm text-slate-700">
          上傳卷證（依上傳順序自動編號）
          <input ref={evidenceRef} type="file" accept=".pdf,.docx,.txt" className="ml-2" onChange={uploadEvidence} />
        </label>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-800">攻防輪次</h2>
          <button type="button" onClick={newRound} className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
            建立新一輪
          </button>
        </div>
        {data.rounds.map((r) => <RoundPanel key={r.id} caseId={caseId} round={r} parties={data.parties} onChanged={load} />)}
      </section>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

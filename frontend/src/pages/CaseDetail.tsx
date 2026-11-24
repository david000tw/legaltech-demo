import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import * as api from '../api'
import {
  attorneyProblem, headerDraftFrom, headerInput, toDrafts, toInputs, type HeaderDraft, type PartyDraft,
} from '../caseForms'
import CaseHeaderFields from '../components/CaseHeaderFields'
import PartiesEditor from '../components/PartiesEditor'
import RequestItemsEditor from '../components/RequestItemsEditor'

const POLL_MS = 3000
const STATUS_LABEL: Record<api.RoundStatus, string> = {
  created: '尚未執行', extracting: '拆解中…', extracted: '待確認', drafting: '起草中…', drafted: '草稿完成', failed: '執行失敗',
}

const partyLabel = (p: api.Party) => `${p.role} ${p.name}`
const KIND_LABEL: Record<api.BriefKind, string> = { complaint: '起訴狀', answer: '答辯狀', preparatory: '準備書狀' }

function RoundPanel({ caseId, caseType, round, parties, briefs, onChanged }: {
  caseId: string; caseType: string; round: api.RoundSummary; parties: api.Party[]; briefs: api.CaseDocument[]
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<api.RoundDetail | null>(null)
  // 起訴狀的案情說明與聲明設定：律師改過的值優先，否則用後端存的值
  const [narrative, setNarrative] = useState<string | null>(null)
  const [joint, setJoint] = useState<boolean | null>(null)
  const [provisional, setProvisional] = useState<boolean | null>(null)
  const [deceased, setDeceased] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // 遞狀人只能是對造或立場相同者；只有一位對造時預設是他，好幾位時預設未指定（避免張冠李戴）
  const filers = parties.filter((p) => p.alignment !== 'ours')
  const opposing = parties.filter((p) => p.alignment === 'opposing')
  const [chosenFiler, setChosenFiler] = useState<string | null>(null)
  const defaultFiler = opposing.length === 1 ? opposing[0].id : ''
  const filer = chosenFiler !== null && (chosenFiler === '' || filers.some((p) => p.id === chosenFiler))
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
    if (status !== 'extracting' && status !== 'drafting') return
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

  async function changeFiler(doc: api.CaseDocument, partyId: string) {
    setMessage(null)
    try {
      await api.setDocumentFiler(doc.id, partyId || null)
      onChanged()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setMessage(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const kind = detail?.brief_kind ?? round.brief_kind ?? 'preparatory'
  const complaint = kind === 'complaint'
  const narrativeText = narrative ?? detail?.narrative ?? ''
  const jointValue = joint ?? detail?.joint_liability ?? false
  const provisionalValue = provisional ?? detail?.provisional_execution ?? true
  const deceasedValue = deceased ?? detail?.statement_variant === 'deceased'
  // 起訴狀：先存案情說明與聲明設定，再拆解（AI 依案情說明與卷證整理請求項目）
  const extract = () => act(async () => {
    if (complaint) {
      await api.patchRound(round.id, { narrative: narrativeText, joint_liability: jointValue,
        provisional_execution: provisionalValue,
        ...(caseType === 'nominee_registration' ? { statement_variant: deceasedValue ? 'deceased' : 'alive' } : {}) })
    }
    await api.extractRound(round.id)
  })
  const copy = () => act(async () => { await api.copyRound(round.id); onChanged() })
  const draft = () => act(() => api.draftRound(round.id))
  // 重新拆解：律師改過勾選時後端要求確認，確認後再送一次
  const reset = () => act(async () => {
    try {
      await api.resetRound(round.id, false)
    } catch (e) {
      if (!(e instanceof api.ApiError) || e.code !== 'confirm_reset') throw e
      if (window.confirm(e.message)) await api.resetRound(round.id, true)
    }
  })
  async function tick(claim: api.ClaimView, selected: boolean) {
    setMessage(null)
    try {
      await api.patchClaim(claim.id, { selected })
      await refresh()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  const error = detail?.error ?? round.error
  const failedStage = detail?.failed_stage ?? null
  const stale = !!detail?.stale
  // 可以上傳書狀、按「拆解」：還沒拆解、拆解失敗、或拆解逾時
  const editable = status === 'created' || (status === 'failed' && failedStage !== 'draft')
    || (status === 'extracting' && stale)
  const claims = (detail?.claims ?? []).filter((c) => c.kind !== 'request')
  const requests = (detail?.claims ?? []).filter((c) => c.kind === 'request')
  const unticked = claims.filter((c) => !c.selected).length
  const unconfirmed = requests.filter((c) => !c.confirmed).length
  // 起草前提：準備書狀、答辯狀至少勾一條主張；起訴狀每個請求項目都要律師確認
  const draftBlockedReason = complaint
    ? (requests.length === 0 ? '還沒有任何請求項目' : unconfirmed > 0 ? `還有 ${unconfirmed} 個請求項目尚未確認` : null)
    : (claims.length > 0 && claims.every((c) => !c.selected) ? '沒有勾選任何主張' : null)
  const byId = new Map(parties.map((p) => [p.id, p]))
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-800">
          {`第${round.seq}輪・${KIND_LABEL[kind]}`}
          {round.copied_from_seq ? <span className="ml-1 text-sm font-normal text-slate-500">{`（由第${round.copied_from_seq}輪複製）`}</span> : null}
        </h3>
        <span className="text-sm text-slate-600">{STATUS_LABEL[status]}{stale ? '（逾時，可重新開始）' : ''}</span>
      </div>
      {stale && (status === 'extracting' || status === 'drafting') && (
        <button type="button" onClick={status === 'extracting' ? extract : draft} disabled={busy}
          className="mt-2 rounded border border-amber-400 px-3 py-1 text-sm text-amber-800 disabled:opacity-50">
          重新開始
        </button>
      )}
      {!stale && (status === 'extracting' || status === 'drafting') && (
        <p className="mt-2 text-sm text-slate-600">AI 處理中，請稍候</p>
      )}
      {status === 'failed' && error && <p className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {briefs.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-slate-700">
          {briefs.map((d) => (
            <li key={d.id}>
              {editable && filers.length > 0 ? (
                <label>
                  {d.filename}
                  <select aria-label={`${d.filename} 的遞狀人`} value={d.filed_by_party_id ?? ''}
                    onChange={(e) => changeFiler(d, e.target.value)}
                    className="ml-2 rounded border border-slate-300 px-2 py-0.5 text-sm">
                    <option value="">遞狀人未指定</option>
                    {filers.map((p) => <option key={p.id} value={p.id}>{partyLabel(p)}</option>)}
                  </select>
                </label>
              ) : (
                <span>{d.filename}：{d.filed_by_party_id && byId.get(d.filed_by_party_id)
                  ? `遞狀人 ${partyLabel(byId.get(d.filed_by_party_id)!)}` : '遞狀人未指定'}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable && complaint && (
        <div className="mt-3 space-y-2 text-sm">
          <label className="block text-slate-700">
            案情說明
            <textarea aria-label="案情說明" value={narrativeText} onChange={(e) => setNarrative(e.target.value)} rows={4}
              placeholder="說明當事人要請求什麼、為什麼（AI 依此與卷證整理請求項目）"
              className="mt-1 w-full rounded border border-slate-300 p-2" />
          </label>
          <div className="flex flex-wrap gap-4 text-slate-700">
            <label><input type="checkbox" checked={jointValue} onChange={(e) => setJoint(e.target.checked)} className="mr-1" />
              被告連帶給付</label>
            <label><input type="checkbox" checked={provisionalValue} onChange={(e) => setProvisional(e.target.checked)}
              className="mr-1" />聲請假執行（只適用金錢請求）</label>
            {caseType === 'nominee_registration' && (
              <label><input type="checkbox" checked={deceasedValue} onChange={(e) => setDeceased(e.target.checked)}
                className="mr-1" />出名人已死亡（起訴繼承人）</label>
            )}
          </div>
          {!(status === 'extracting' && stale) && (
            <button type="button" onClick={extract} disabled={busy || !narrativeText.trim()}
              className="rounded bg-indigo-600 px-3 py-1.5 font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              拆解
            </button>
          )}
        </div>
      )}
      {editable && !complaint && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {filers.length > 0 && (
            <label className="text-sm text-slate-700">
              遞狀人
              <select aria-label="遞狀人" value={filer} onChange={(e) => setChosenFiler(e.target.value)} disabled={busy}
                className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
                <option value="">（未指定）</option>
                {filers.map((p) => <option key={p.id} value={p.id}>{partyLabel(p)}</option>)}
              </select>
            </label>
          )}
          {opposing.length > 1 && !filer && (
            <span className="text-xs text-amber-700">對造有好幾位：請選這份書狀的遞狀人，AI 才知道是誰提出的主張。</span>
          )}
          <label className="text-sm text-slate-700">
            上傳對造書狀
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" className="ml-2 text-sm" onChange={upload} disabled={busy} />
          </label>
          {!(status === 'extracting' && stale) && (
            <button type="button" onClick={extract} disabled={busy}
              className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              拆解
            </button>
          )}
        </div>
      )}
      {(status === 'extracted' || (status === 'failed' && failedStage === 'draft')) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={draft} disabled={busy || !!draftBlockedReason}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {status === 'failed' ? '重新起草' : '起草'}
          </button>
          <button type="button" onClick={reset} disabled={busy}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 disabled:opacity-50">
            重新拆解
          </button>
          {draftBlockedReason && <span className="text-xs text-amber-700">{draftBlockedReason}</span>}
          {unticked > 0 && <span className="text-xs text-amber-700">未勾選的 {unticked} 條主張不會被回應</span>}
        </div>
      )}
      {status === 'extracted' && complaint && detail && (
        <RequestItemsEditor round={detail} onChanged={refresh} onError={setMessage} />
      )}
      {status === 'drafted' && detail?.draft_id && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Link to={`/drafts/${detail.draft_id}`} className="text-sm font-medium text-indigo-700 underline">開啟草稿</Link>
          <button type="button" onClick={copy} disabled={busy}
            className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 disabled:opacity-50">
            以這一輪為基礎重新起草
          </button>
        </div>
      )}
      {claims.length > 0 && (
        <ol className="mt-3 space-y-1 text-sm text-slate-700">
          {claims.map((c) => (
            <li key={c.seq}>
              {status === 'extracted' && (
                <input type="checkbox" aria-label={`回應 主張${c.seq}`} checked={c.selected} className="mr-1"
                  onChange={(e) => tick(c, e.target.checked)} />
              )}
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
  // 案件層只決定民事／家事；起訴狀、答辯狀或準備書狀依各輪種類
  const procedureLabel = data.procedure === 'family' ? '家事' : '民事'
  const kindTitles = (data.brief_kind_options ?? []).map((o) => procedureLabel + o.label).join('／')
  const briefTitles = kindTitles ? `${kindTitles}（依各輪種類）` : data.brief_title

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    const problem = attorneyProblem(draft.attorneys)
    if (problem) {
      onError(problem)
      return
    }
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
          <dt className="text-slate-500">書狀名稱</dt><dd>{briefTitles}</dd>
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
  const navigate = useNavigate()
  const notice = (useLocation().state as { notice?: string } | null)?.notice
  const [data, setData] = useState<api.CaseDetail | null>(null)
  const [newKind, setNewKind] = useState<api.BriefKind | null>(null)  // 建立新一輪時選的書狀種類（沒選＝後端預設）
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
  // 儲存成功：清掉之前的錯誤；當事人存好了，「當事人沒有存成功」的提醒也一併拿掉（重新整理也不會再出現）
  const saved = useCallback(async () => {
    setError(null)
    await load()
  }, [load])
  const partiesSaved = useCallback(async () => {
    if (notice) navigate(`/cases/${caseId}`, { replace: true, state: null })
    await saved()
  }, [notice, navigate, caseId, saved])
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
      await api.createRound(caseId, newKind ?? data?.default_brief_kind)
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

      <HeaderSection data={data} options={options} onSaved={saved} onError={setError} />
      <PartiesSection data={data} options={options} onSaved={partiesSaved} onError={setError} />

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
          <div className="flex items-center gap-2">
            {(data.brief_kind_options ?? []).length > 0 && (
              <select aria-label="書狀種類" value={newKind ?? data.default_brief_kind ?? ''}
                onChange={(e) => setNewKind(e.target.value as api.BriefKind)}
                className="rounded border border-slate-300 px-2 py-1.5 text-sm">
                {(data.brief_kind_options ?? []).map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
              </select>
            )}
            <button type="button" onClick={newRound} className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
              建立新一輪
            </button>
          </div>
        </div>
        {data.rounds.map((r) => (
          <RoundPanel key={r.id} caseId={caseId} caseType={data.case_type} round={r} parties={data.parties} onChanged={load}
            briefs={data.documents.filter((d) => d.kind === 'opponent_brief' && d.round_id === r.id)} />
        ))}
      </section>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

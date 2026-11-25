import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import * as api from '../api'
import {
  attorneyProblem, headerDraftFrom, headerInput, toDrafts, toInputs, type HeaderDraft, type PartyDraft,
} from '../caseForms'
import { busyText, POLL_MS } from '../aiJobs'
import AppShell from '../components/AppShell'
import CaseHeaderFields from '../components/CaseHeaderFields'
import EvidenceNotesSection from '../components/EvidenceNotesSection'
import HealthTab from '../components/HealthTab'
import JudgmentSearchTab from '../components/JudgmentSearchTab'
import OcrStatus from '../components/OcrStatus'
import PageCheckPanel from '../components/PageCheckPanel'
import PartiesEditor from '../components/PartiesEditor'
import RequestItemsEditor from '../components/RequestItemsEditor'
import SourcePane from '../components/SourcePane'
import TimelineSection from '../components/TimelineSection'
import { KIND_LABEL, UPLOAD_ACCEPT, UPLOAD_HINT } from '../labels'
import { OCR_TAG } from '../components/SourceBadge'
import { useMe } from '../MeContext'
import { confirmOcr } from '../ocrConfirm'
import { BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL, CARD } from '../ui'

const STATUS_LABEL: Record<api.RoundStatus, string> = {
  created: '尚未執行', extracting: '拆解中…', extracted: '待確認', drafting: '起草中…', drafted: '草稿完成', failed: '執行失敗',
}

const partyLabel = (p: api.Party) => `${p.role} ${p.name}`

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
  const [checkPage, setCheckPage] = useState<string | null>(null)  // 輪次卡片內的對照原圖面板（計畫 2A）
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

  // 辨識狀態變了：案件（對造書狀的頁）與輪次（拆解前把關）都要重新讀
  const ocrChanged = useCallback(async () => {
    onChanged()
    await refresh()
  }, [onChanged, refresh])

  async function upload() {
    const files = Array.from(fileRef.current?.files ?? [])
    if (files.length === 0) return
    setBusy(true)
    setMessage(null)
    try {
      // 多張照片合成一份對造書狀；掃描頁會自動辨識
      await api.uploadDocuments(caseId, files, 'opponent_brief', round.id, filer || undefined)
      await ocrChanged()
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
  const pendingEvidence = detail?.pending_ocr_pages ?? []
  const extract = () => act(async () => {
    // 起訴狀：卷證還有頁沒辨識完，AI 整理的請求項目可能不完整，先問律師
    if (complaint && pendingEvidence.length > 0
      && !window.confirm(`卷證${pendingEvidence.join('、')}尚未辨識，請求項目可能不完整，要先拆解嗎？`)) return
    if (complaint) {
      await api.patchRound(round.id, { narrative: narrativeText, joint_liability: jointValue,
        provisional_execution: provisionalValue,
        ...(caseType === 'nominee_registration' ? { statement_variant: deceasedValue ? 'deceased' : 'alive' } : {}) })
    }
    await api.extractRound(round.id)
  })
  // 案情說明離開欄位就存：沒按「拆解」就重新整理也不會不見。不設 busy，免得緊接著按「拆解」時按鈕已被停用
  async function saveNarrative() {
    if (narrative === null || narrative === (detail?.narrative ?? '')) return
    setMessage(null)
    try {
      await api.patchRound(round.id, { narrative })
      await refresh()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }
  // 起訴狀聲明設定一勾就存：切到別的分頁（分頁會重新載入）或重新整理，都不會悄悄變回預設值
  async function saveOption(body: api.RoundSettings) {
    setMessage(null)
    try {
      await api.patchRound(round.id, body)
      await refresh()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }
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
        <p className="mt-2 text-sm text-slate-600">{busyText(status === 'extracting' ? 'extract' : 'draft')}</p>
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
              <OcrStatus doc={d} onChanged={ocrChanged} onOpenPage={setCheckPage} />
            </li>
          ))}
        </ul>
      )}
      {editable && complaint && (
        <div className="mt-3 space-y-2 text-sm">
          <label className="block text-slate-700">
            案情說明
            <textarea aria-label="案情說明" value={narrativeText} onChange={(e) => setNarrative(e.target.value)}
              onBlur={() => void saveNarrative()} rows={4}
              placeholder="說明當事人要請求什麼、為什麼（AI 依此與卷證整理請求項目）"
              className="mt-1 w-full rounded border border-slate-300 p-2" />
          </label>
          <div className="flex flex-wrap gap-4 text-slate-700">
            <label><input type="checkbox" checked={jointValue} className="mr-1" onChange={(e) => {
              setJoint(e.target.checked)
              void saveOption({ joint_liability: e.target.checked })
            }} />被告連帶給付</label>
            <label><input type="checkbox" checked={provisionalValue} className="mr-1" onChange={(e) => {
              setProvisional(e.target.checked)
              void saveOption({ provisional_execution: e.target.checked })
            }} />聲請假執行（只適用金錢請求）</label>
            {caseType === 'nominee_registration' && (
              <label><input type="checkbox" checked={deceasedValue} className="mr-1" onChange={(e) => {
                setDeceased(e.target.checked)
                void saveOption({ statement_variant: e.target.checked ? 'deceased' : 'alive' })
              }} />出名人已死亡（起訴繼承人）</label>
            )}
          </div>
          {!(status === 'extracting' && stale) && (
            <button type="button" onClick={extract} disabled={busy || !narrativeText.trim()}
              className={BTN_SECONDARY}>
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
            <input ref={fileRef} type="file" accept={UPLOAD_ACCEPT} multiple className="ml-2 text-sm" onChange={upload}
              disabled={busy} />
          </label>
          {!(status === 'extracting' && stale) && (
            <button type="button" onClick={extract} disabled={busy || !!detail?.extract_blocked}
              className={BTN_SECONDARY}>
              拆解
            </button>
          )}
          {detail?.extract_blocked && <span className="text-xs text-amber-700">{detail.extract_blocked}</span>}
          <span className="w-full text-xs text-slate-500">{UPLOAD_HINT}</span>
        </div>
      )}
      {(status === 'extracted' || (status === 'failed' && failedStage === 'draft')) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={draft} disabled={busy || !!draftBlockedReason}
            className={BTN_SECONDARY}>
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
        <RequestItemsEditor round={detail} onChanged={refresh} onError={setMessage} onOpenPage={setCheckPage} />
      )}
      {checkPage && (
        <div className="mt-3 space-y-2 rounded border border-slate-200 p-3">
          <div className="flex justify-end">
            <button type="button" className={BTN_SMALL} onClick={() => setCheckPage(null)}>關閉</button>
          </div>
          <PageCheckPanel key={checkPage} pageId={checkPage} canReview={!!detail?.can_review} onChanged={ocrChanged} />
        </div>
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
              {c.quote && (
                <div title={c.quote} className="ml-4 line-clamp-2 text-xs text-slate-500">
                  {`出處：${c.page_no != null ? `第 ${c.page_no} 頁` : ''}「${c.quote}」`}
                </div>
              )}
              {c.ocr_unverified && c.page_id && (
                <>
                  <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">{OCR_TAG}</span>
                  <button type="button" onClick={() => setCheckPage(c.page_id as string)}
                    className="ml-1 rounded border border-slate-300 px-1.5 py-0.5 text-xs text-slate-700">看原圖</button>
                </>
              )}
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

// 狀頭、當事人改到一半的內容由案件頁保管（draft、setDraft）：切到別的分頁再回來，表單和沒存的修改都還在
function HeaderSection({ data, options, draft, setDraft, onSaved, onError }: {
  data: api.CaseDetail; options: api.CaseOptions | null
  draft: HeaderDraft | null; setDraft: (d: HeaderDraft | null) => void
  onSaved: () => Promise<void>; onError: (m: string) => void
}) {
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
          <button type="button" onClick={() => setDraft(headerDraftFrom(data))} className={BTN_PRIMARY}>修改狀頭</button>
        )}
      </div>
      {data.procedure_warning && (
        <p className="rounded bg-yellow-50 p-2 text-sm text-yellow-800">{data.procedure_warning}</p>
      )}
      {draft ? (
        <form onSubmit={save} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <CaseHeaderFields value={draft} onChange={setDraft} options={options} addresseeDefault={data.addressee_default} />
          <div className="flex gap-2">
            <button type="submit" className={BTN_SECONDARY}>儲存狀頭</button>
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

function PartiesSection({ data, options, draft, setDraft, onSaved, onError }: {
  data: api.CaseDetail; options: api.CaseOptions | null
  draft: PartyDraft[] | null; setDraft: (d: PartyDraft[] | null) => void
  onSaved: () => Promise<void>; onError: (m: string) => void
}) {
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
          <button type="button" onClick={() => setDraft(toDrafts(data.parties))} className={BTN_SECONDARY}>修改當事人</button>
        )}
      </div>
      {draft ? (
        <form onSubmit={save} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <PartiesEditor value={draft} onChange={setDraft} options={options} />
          <p className="text-xs text-slate-500">刪除的當事人若是某份對造書狀的遞狀人，該書狀的遞狀人會變成未指定。</p>
          <div className="flex gap-2">
            <button type="submit" className={BTN_SECONDARY}>儲存當事人</button>
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

function ExhibitRow({ exhibit, doc, onSaved, onError, onOpenPage }: {
  exhibit: api.Exhibit
  doc: api.CaseDocument | undefined
  onSaved: () => Promise<void>
  onError: (m: string) => void
  onOpenPage: (pageId: string) => void
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
    <li className="px-4 py-2 text-sm">
     <div className="flex items-center gap-3">
      <span className="font-medium text-slate-900">{exhibit.label}</span>
      <span className="text-slate-600">{exhibit.filename}</span>
      <span className="ml-auto flex items-center gap-2">
        {editing ? (
          <>
            <input type="number" min={1} value={num} onChange={(e) => setNum(e.target.value)}
              aria-label={`${exhibit.label} 新號碼`} className="w-16 rounded border border-slate-300 px-1 py-0.5" />
            <button type="button" onClick={save} disabled={!(Number(num) >= 1)}
              className={BTN_SMALL}>儲存號碼</button>
            <button type="button" onClick={() => { setEditing(false); setNum(String(exhibit.number)) }}
              className="rounded border px-2 py-0.5 text-xs">取消</button>
          </>
        ) : (
          <button type="button" onClick={() => setEditing(true)} aria-label={`改號 ${exhibit.label}`}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700">改號</button>
        )}
      </span>
     </div>
      {doc && <OcrStatus doc={doc} onChanged={onSaved} onOpenPage={onOpenPage} />}
    </li>
  )
}

const TABS = [
  ['info', '案件資料'], ['evidence', '卷證與時間軸'], ['briefs', '書狀'], ['search', '判決檢索'], ['health', '書狀健檢'],
] as const
type TabKey = typeof TABS[number][0]

export default function CaseDetail() {
  const { caseId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const notice = (location.state as { notice?: string } | null)?.notice
  const [params, setParams] = useSearchParams()
  const tab: TabKey = TABS.find(([k]) => k === params.get('tab'))?.[0] ?? 'info'
  const [data, setData] = useState<api.CaseDetail | null>(null)
  const [newKind, setNewKind] = useState<api.BriefKind | null>(null)  // 建立新一輪時選的書狀種類（沒選＝後端預設）
  const [options, setOptions] = useState<api.CaseOptions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [source, setSource] = useState<api.CitationSource | null>(null)  // 卷證、健檢分頁右側的原文
  const [checkPage, setCheckPage] = useState<string | null>(null)  // 右側的對照原圖面板（計畫 2A）
  const me = useMe()
  const canReview = me?.role === 'lawyer' && me.bar_verified
  const [headerDraft, setHeaderDraft] = useState<HeaderDraft | null>(null)  // 修改中的狀頭（null＝沒在改）
  const [partyDraft, setPartyDraft] = useState<PartyDraft[] | null>(null)  // 修改中的當事人
  const evidenceRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)  // 卷證上傳處理中：再選一次檔案會多建一個證物編號

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

  // 分頁存在網址（?tab=），重新整理或分享連結都回到同一個分頁；沒選中的分頁不渲染，切回來時重新讀取
  function selectTab(key: TabKey) {
    const next = new URLSearchParams(params)
    next.set('tab', key)
    setParams(next, { replace: true, state: location.state })
    setSource(null)
    setCheckPage(null)
  }

  async function uploadEvidence() {
    const files = Array.from(evidenceRef.current?.files ?? [])
    if (files.length === 0 || uploading) return
    setUploading(true)
    try {
      await api.uploadDocuments(caseId, files, 'evidence')  // 多張照片合成一份卷證、一個證物號
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
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

  // 還沒有進行中工作的待辨識頁：「開始辨識（N 頁）」
  async function startCaseOcr() {
    try {
      if ((await confirmOcr((c) => api.startCaseOcr(caseId, c))) !== null) await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  function openPage(pageId: string) {
    setSource(null)
    setCheckPage(pageId)
  }

  // 卷證說明、大事記、健檢的出處：在右側卡片顯示那一頁的原文並標出摘錄
  async function showSource(s: api.SourceView) {
    if (!s.page_id) return
    setCheckPage(null)
    try {
      setSource(await api.getPageSource(s.page_id, s.quote))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!data) return <p className="p-6 text-slate-600">{error ?? '載入中…'}</p>
  const docs = new Map(data.documents.map((d) => [d.id, d]))
  const label = TABS.find(([k]) => k === tab)?.[1] ?? ''
  const sourceCard = checkPage ? (
    <aside className={`${CARD} h-fit space-y-2 lg:sticky lg:top-4 lg:max-h-[90vh] lg:overflow-auto`}>
      <div className="flex justify-end">
        <button type="button" className={BTN_SMALL} onClick={() => setCheckPage(null)}>關閉</button>
      </div>
      <PageCheckPanel key={checkPage} pageId={checkPage} canReview={canReview} onChanged={load} />
    </aside>
  ) : source && (
    <aside className={`${CARD} h-fit lg:sticky lg:top-4 lg:max-h-[85vh] lg:overflow-auto`}>
      <SourcePane source={source} />
    </aside>
  )
  const pendingOcr = data.pending_ocr_pages ?? 0

  return (
    <AppShell caseTitle={data.title} caseId={caseId}>
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{data.title}</h1>
          <p className="text-sm text-slate-600">我方：{data.our_side === 'plaintiff' ? '原告' : '被告'}</p>
          {notice && <p className="mt-2 rounded bg-yellow-50 p-2 text-sm text-yellow-800">{notice}</p>}
        </div>

        <div role="tablist" aria-label="案件分頁" className="flex flex-wrap border-b border-slate-200 text-sm">
          {TABS.map(([key, name]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => selectTab(key)}
              className={`px-4 py-2 ${tab === key
                ? 'border-b-2 border-amber-500 font-medium text-slate-900' : 'text-slate-600 hover:text-slate-900'}`}>
              {name}
            </button>
          ))}
        </div>

        <div role="tabpanel" aria-label={label} className="space-y-6">
          {tab === 'info' && (
            <>
              <HeaderSection data={data} options={options} draft={headerDraft} setDraft={setHeaderDraft}
                onSaved={saved} onError={setError} />
              <PartiesSection data={data} options={options} draft={partyDraft} setDraft={setPartyDraft}
                onSaved={partiesSaved} onError={setError} />
            </>
          )}

          {tab === 'evidence' && (
            <div className={checkPage ? 'grid gap-4 lg:grid-cols-[1fr_40rem]'
              : source ? 'grid gap-4 lg:grid-cols-[1fr_24rem]' : ''}>
              <div className="space-y-6">
                <section className="space-y-2">
                  <div className="flex flex-wrap items-center gap-3">
                    <button type="button" onClick={() => evidenceRef.current?.click()} disabled={uploading}
                      className={BTN_PRIMARY}>
                      {uploading ? '上傳中…' : '上傳卷證'}
                    </button>
                    <label className="sr-only">
                      上傳卷證（依上傳順序自動編號）
                      <input ref={evidenceRef} type="file" accept={UPLOAD_ACCEPT} multiple disabled={uploading}
                        onChange={uploadEvidence} />
                    </label>
                    <span className="text-xs text-slate-500">依上傳順序自動編號；多張照片會合成一份卷證</span>
                    {pendingOcr > 0 && (
                      <button type="button" onClick={startCaseOcr} className={BTN_SECONDARY}>
                        {`開始辨識（${pendingOcr} 頁）`}
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-slate-500">{UPLOAD_HINT}</p>
                  <h2 className="text-lg font-semibold text-slate-800">卷證</h2>
                  <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                    {data.exhibits.map((e) => (
                      <ExhibitRow key={`${e.id}-${e.number}`} exhibit={e} onSaved={load} onError={setError}
                        doc={docs.get(e.document_id)} onOpenPage={openPage} />
                    ))}
                    {data.exhibits.length === 0 && <li className="px-4 py-2 text-sm text-slate-500">尚未上傳卷證</li>}
                  </ul>
                </section>
                {/* 卷證說明與大事記（設計 5.6、5.7）：只給律師參考，不進書狀 */}
                <EvidenceNotesSection key={`notes-${data.exhibits.length}`} caseId={caseId} onShowSource={showSource} />
                <TimelineSection key={`timeline-${data.documents.length}`} caseId={caseId} onShowSource={showSource} />
              </div>
              {sourceCard}
            </div>
          )}

          {tab === 'briefs' && (
            <section className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={newRound} className={BTN_PRIMARY}>建立新一輪</button>
                {(data.brief_kind_options ?? []).length > 0 && (
                  <select aria-label="書狀種類" value={newKind ?? data.default_brief_kind ?? ''}
                    onChange={(e) => setNewKind(e.target.value as api.BriefKind)}
                    className="rounded border border-slate-300 px-2 py-1.5 text-sm">
                    {(data.brief_kind_options ?? []).map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
                  </select>
                )}
              </div>
              <h2 className="text-lg font-semibold text-slate-800">攻防輪次</h2>
              {data.rounds.map((r) => (
                <RoundPanel key={r.id} caseId={caseId} caseType={data.case_type} round={r} parties={data.parties}
                  onChanged={load}
                  briefs={data.documents.filter((d) => d.kind === 'opponent_brief' && d.round_id === r.id)} />
              ))}
            </section>
          )}

          {tab === 'search' && <JudgmentSearchTab caseId={caseId} />}

          {tab === 'health' && (
            <div className={source ? 'grid gap-4 lg:grid-cols-[1fr_24rem]' : ''}>
              <HealthTab rounds={data.rounds} onShowSource={showSource} />
              {sourceCard}
            </div>
          )}
        </div>
        {error && <p className="text-sm text-red-700">{error}</p>}
      </div>
    </AppShell>
  )
}

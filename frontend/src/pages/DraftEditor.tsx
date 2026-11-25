import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import * as api from '../api'
import AppShell from '../components/AppShell'
import CitationTag from '../components/CitationTag'
import EvidenceNotesSection from '../components/EvidenceNotesSection'
import ExportButton, { errorMessage, reasonLabel } from '../components/ExportButton'
import FilingPackageButton from '../components/FilingPackageButton'
import KeyPointsList from '../components/KeyPointsList'
import PinnedPanel from '../components/PinnedPanel'
import SourcePane from '../components/SourcePane'
import StatementBlock, { AmountButtons, ClaimValueField } from '../components/StatementBlock'
import CitationEditor from '../components/CitationEditor'
import SupportBadge from '../components/SupportBadge'
import TimelineSection from '../components/TimelineSection'
import { POLL_MS, useJobPolling } from '../aiJobs'
import { BTN_SECONDARY } from '../ui'

// 草稿頁左側：原文對照（引用與出處的原文）、卷證說明、大事記（設計 5.6、5.7，只看不改）
const SIDE_TABS = [['source', '原文對照'], ['notes', '卷證說明'], ['timeline', '大事記'], ['pinned', '已釘選判決']] as const
type SideTab = typeof SIDE_TABS[number][0]

function ParagraphCard({ p, draftId, canReview, onShowSource, onChanged, onError }: {
  p: api.ParagraphView
  draftId: string
  canReview: boolean
  onShowSource: (c: api.CitationView) => void
  onChanged: () => Promise<void>
  onError: (m: string | null) => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [heading, setHeading] = useState(p.heading_text)
  const [text, setText] = useState(p.text)
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)

  async function act(f: () => Promise<unknown>) {
    setBusy(true)
    onError(null)  // 新的操作開始，舊的錯誤訊息不再適用
    try {
      await f()
      await onChanged()
    } catch (e) {
      onError(errorMessage(e))
      await onChanged()  // 例如內容已被別人改過：重新載入，讓律師看到最新版本
    } finally {
      setBusy(false)
    }
  }

  const save = (andReview = false) => act(async () => {
    // 內文已拿掉標記的引用就不再送出，律師可以自己移除驗證失敗的引用
    const used = new Set([...text.matchAll(/\{\{(?:cite|quote):([A-Z]\d+)\}\}/g)].map((m) => m[1]))
    const kept = p.citations.filter((c) => used.has(c.code)).map((c) => ({ code: c.code, quote: c.quote }))
    const saved = await api.patchParagraph(p.id, text, kept, heading)
    setEditing(false)
    // 「儲存並標記已審」：用存完後的版本標記；被擋下時修改已經存好，畫面顯示原因
    if (andReview) await api.reviewParagraph(p.id, saved.version, { defer: true })
  })

  // 標題與內文都清空＝刪除本段（後端保留修改紀錄）
  const remove = () => act(() => api.patchParagraph(p.id, '', [], ''))

  return (
    <article id={`paragraph-${p.seq}`} className="space-y-2 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-900">
          {p.display_heading}
          {p.source_notice && (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-normal text-amber-800">
              {p.source_notice}
            </span>
          )}
        </h3>
        <span className={`text-xs ${p.status === 'reviewed' ? 'text-emerald-700' : 'text-slate-500'}`}>
          {p.status === 'reviewed' ? `已審（${p.reviewed_bar_number ?? ''}）` : '未審'}
        </span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <label className="block text-xs text-slate-600">
            段落標題
            <input aria-label="段落標題" value={heading} onChange={(e) => setHeading(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 p-2 text-sm" />
          </label>
          <div className="block text-xs text-slate-600">
            段落內文
            {/* 出處是一整塊，打不壞；要新增出處用「要求重寫」（設計 5.3） */}
            <CitationEditor value={text} citations={p.citations} onChange={setText} ariaLabel="段落內文" />
          </div>
          <div className="flex gap-2">
            {canReview ? (
              <>
                <button type="button" onClick={() => save(true)} disabled={busy}
                  className="rounded bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-50">儲存並標記已審</button>
                <button type="button" onClick={() => save()} disabled={busy} className={BTN_SECONDARY}>只儲存</button>
              </>
            ) : (
              <button type="button" onClick={() => save()} disabled={busy} className={BTN_SECONDARY}>儲存</button>
            )}
            <button type="button" onClick={() => { setEditing(false); setText(p.text); setHeading(p.heading_text) }}
              className="rounded border px-3 py-1 text-sm">取消</button>
          </div>
        </div>
      ) : (
        <p className="font-kai whitespace-pre-wrap leading-7 text-slate-800">{p.rendered_text}</p>
      )}
      {p.review_requested && (
        <p className="text-xs text-slate-600">支持度檢查中，查完全部是「支持」會自動標記已審</p>
      )}
      {p.review_note && <p className="text-xs text-amber-700">{p.review_note}</p>}
      {p.verify_reasons.length > 0 && (
        <ul className="text-sm text-red-700">{p.verify_reasons.map((r) => <li key={r}>{reasonLabel(r)}</li>)}</ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {p.citations.map((c) => (
          <span key={c.id} className="inline-flex items-center gap-1">
            <CitationTag citation={c} onClick={() => onShowSource(c)} />
            {canReview && c.kind === 'judgment' && !c.lawyer_confirmed && c.opened !== true
              && (c.verify_status === 'ok' || (c.verify_status === 'warn' && c.court_confirmed)) && (
              // 按「已審」會一併確認判決引用，前提是律師本人打開過被引用的那一段（設計 5.1）
              <button type="button" onClick={() => onShowSource(c)}
                className="rounded border border-amber-400 px-2 py-0.5 text-xs text-amber-800">
                {`還要先看原文：${c.label ?? c.code}`}
              </button>
            )}
          </span>
        ))}
      </div>
      {p.citations.some((c) => c.support) && (
        <div className="space-y-1">
          {p.citations.map((c) => (
            <SupportBadge key={c.id} citation={c} draftId={draftId} canReview={canReview} busy={busy}
              onAction={act} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
        <button type="button" onClick={() => setEditing(true)} className="rounded border px-3 py-1 text-sm">修改</button>
        {confirmDelete ? (
          <>
            <button type="button" disabled={busy} onClick={remove}
              className="rounded bg-red-600 px-3 py-1 text-sm text-white">確定刪除</button>
            <button type="button" onClick={() => setConfirmDelete(false)} className="rounded border px-3 py-1 text-sm">不刪除</button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirmDelete(true)}
            className="rounded border border-red-300 px-3 py-1 text-sm text-red-700">刪除本段</button>
        )}
        <input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="重寫指示，例如：語氣更精簡"
          aria-label="重寫指示" className="min-w-48 flex-1 rounded border border-slate-300 px-2 py-1 text-sm" />
        <button type="button" disabled={busy || !instruction.trim()}
          onClick={() => act(async () => { await api.rewriteParagraph(p.id, instruction); setInstruction('') })}
          className="rounded border px-3 py-1 text-sm disabled:opacity-50">要求重寫</button>
        <AmountButtons reasons={p.verify_reasons} canReview={canReview}
          onConfirm={(n) => act(() => api.confirmAmount(p.id, n))} />
        {canReview && (
          <button type="button" disabled={busy} onClick={() => act(() => api.reviewParagraph(p.id, p.version))}
            className="rounded bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-50">標記已審</button>
        )}
      </div>
    </article>
  )
}

export default function DraftEditor({ pollMs = POLL_MS }: { pollMs?: number } = {}) {
  const { draftId = '' } = useParams()
  const [draft, setDraft] = useState<api.DraftView | null>(null)
  const [source, setSource] = useState<api.CitationSource | null>(null)
  const [sourceCitationId, setSourceCitationId] = useState<string | null>(null)
  const [tab, setTab] = useState<SideTab>('source')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setDraft(await api.getDraft(draftId))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [draftId])

  useEffect(() => { void load() }, [load])

  // 支持度檢查在背景執行（計畫 2B）：還有檢查中的引用時定時重新載入，查完自動更新
  const checking = !!draft && [...draft.paragraphs, ...(draft.statement ? [draft.statement] : [])]
    .some((p) => p.citations.some((c) => c.support?.status === 'pending'))
  useJobPolling(checking, load, pollMs)

  // 阻擋清單點一項：捲到那一段並短暫標亮（整份書狀層級的項目沒有對應段落）
  function jumpTo(b: api.Blocker) {
    const id = b.scope === 'statement' ? 'paragraph-statement' : b.scope === 'draft' ? null : `paragraph-${b.paragraph_seq}`
    const el = id ? document.getElementById(id) : null
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('ring-2', 'ring-amber-400')
    window.setTimeout(() => el.classList.remove('ring-2', 'ring-amber-400'), 2000)
  }

  async function showSource(c: api.CitationView) {
    try {
      setSource(await api.getCitationSource(c.id))
      setSourceCitationId(c.id)
      setTab('source')
      if (c.kind === 'judgment' && c.opened !== true) await load()  // 打開過原文才能按「已審」：重新載入讓提示消失
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // 卷證說明、大事記的出處：顯示那一頁的原文並標出摘錄，切回「原文對照」
  async function showPageSource(s: api.SourceView) {
    if (!s.page_id) return
    try {
      setSource(await api.getPageSource(s.page_id, s.quote))
      setSourceCitationId(null)
      setTab('source')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function doExport() {
    try {
      const blob = await api.exportDraft(draftId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${draft?.brief_title ?? '書狀'}.docx`  // 書狀名稱依程序（民事／家事）
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)  // 立刻釋放可能讓部分瀏覽器中斷下載
    } catch (e) {
      setError((e as Error).message)
      await load()
    }
  }

  // 卷證引用的原文是辨識文字：律師對照原圖後勾「已對照原圖」，再重新讀取草稿（阻擋原因）與原文卡片
  async function verifySource(s: api.CitationSource) {
    if (!s.page_id || s.version == null) return
    setError(null)
    try {
      await api.verifyPage(s.page_id, s.version)
      if (sourceCitationId) setSource(await api.getCitationSource(sourceCitationId))
    } catch (e) {
      setError((e as Error).message)
    }
    await load()
  }

  async function confirmCourt(c: api.CitationView) {
    setError(null)
    try {
      await api.confirmCourt(c.id)
    } catch (e) {
      setError((e as Error).message)
    }
    await load()
  }

  // 原文對照畫面顯示的那筆引用，以最新載入的草稿為準（確認後按鈕自然消失）
  const sourceCitation = draft?.paragraphs.flatMap((p) => p.citations).find((c) => c.id === sourceCitationId) ?? null

  if (!draft) return <p className="p-6 text-slate-600">{error ?? '載入中…'}</p>
  return (
    <AppShell caseTitle={draft.case_title} caseId={draft.case_id}>
    <div className="mx-auto max-w-7xl space-y-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to={`/cases/${draft.case_id}`} className="text-sm text-indigo-700 underline">← 回到案件</Link>
          <h1 className="mt-1 text-2xl font-bold text-slate-900">{draft.brief_title}（草稿）</h1>
          <p className="text-xs text-amber-700">AI 產出的初稿，須由律師逐段審核；引用的判決要先打開原文，按該段「已審」時一併確認。</p>
        </div>
        <div data-testid="export-area" className="max-w-md space-y-2">
          {draft.procedure_warning && (
            <p className="rounded bg-yellow-50 p-2 text-sm text-yellow-800">{draft.procedure_warning}</p>
          )}
          {checking && <p className="rounded bg-slate-100 p-2 text-sm text-slate-700">支持度檢查中</p>}
          <ExportButton blockers={draft.blockers} onExport={doExport} onJump={jumpTo} />
          <FilingPackageButton draftId={draftId} blockers={draft.blockers} canReview={draft.can_review} />
        </div>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {(draft.unverified_ocr_pages ?? []).length > 0 && (
        <p className="rounded bg-amber-50 p-2 text-sm text-amber-900">
          {`本輪資料來源含未對照原圖的辨識文字（${(draft.unverified_ocr_pages ?? []).join('、')}）；日期、姓名、車號等內容請逐段核對原圖。`}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <aside className="rounded-lg border border-slate-200 bg-white lg:sticky lg:top-4 lg:max-h-[85vh] lg:overflow-auto">
          <div role="tablist" aria-label="左側內容" className="flex border-b border-slate-200 text-sm">
            {SIDE_TABS.map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
                className={`px-3 py-2 ${tab === key ? 'border-b-2 border-indigo-600 font-medium text-indigo-700' : 'text-slate-600'}`}>
                {label}
              </button>
            ))}
          </div>
          {tab === 'source' && (
            <SourcePane source={source} citation={sourceCitation} canReview={draft.can_review} onConfirmCourt={confirmCourt}
              onVerify={verifySource} />
          )}
          {tab === 'notes' && (
            <div className="p-3"><EvidenceNotesSection caseId={draft.case_id} compact onShowSource={showPageSource} /></div>
          )}
          {tab === 'timeline' && (
            <div className="p-3"><TimelineSection caseId={draft.case_id} compact onShowSource={showPageSource} /></div>
          )}
          {tab === 'pinned' && (
            <PinnedPanel caseId={draft.case_id} onInserted={() => void load()}
              paragraphs={draft.paragraphs.map((p, i) => ({ id: p.id, no: i + 1, heading: p.heading_text }))} />
          )}
        </aside>
        <div className="space-y-4">
          {draft.key_points && (
            <KeyPointsList data={draft.key_points}
              onJump={(seq) => jumpTo({ paragraph_seq: seq, reason: '', scope: 'paragraph' })} />
          )}
          {draft.claim_value && (
            <ClaimValueField key={`cv-${draft.claim_value.value}`} draft={draft} onChanged={load} onError={setError} />
          )}
          {draft.statement && (
            <StatementBlock key={`${draft.statement.id}-${draft.statement.text}`} draft={draft} onChanged={load}
              onError={setError} />
          )}
          {draft.body_label && <h2 className="text-lg font-semibold text-slate-800">{draft.body_label}</h2>}
          {draft.paragraphs.map((p) => (
            <ParagraphCard key={`${p.id}-${p.text}`} p={p} draftId={draftId} canReview={draft.can_review}
              onShowSource={showSource}
              onChanged={load} onError={setError} />
          ))}
        </div>
      </div>
    </div>
    </AppShell>
  )
}

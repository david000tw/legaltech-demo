// 聲明區塊（訴之聲明、答辯聲明，設計 5.2、5.3）：放在內文前、不佔段落編號，律師審過才能匯出。
// 訴之聲明的金額是系統標記 {{amount}}，畫面顯示系統加總後的金額；律師要改金額得回到請求項目。
import { useState } from 'react'
import * as api from '../api'
import { BTN_SECONDARY } from '../ui'
import { errorMessage, reasonLabel } from './ExportButton'

function amountOf(reason: string): number | null {
  return reason.startsWith('amount_unverified:') ? Number(reason.slice('amount_unverified:'.length)) : null
}

export function AmountButtons({ reasons, canReview, onConfirm }: {
  reasons: string[]; canReview: boolean; onConfirm: (amount: number) => void
}) {
  if (!canReview) return null
  const amounts = reasons.map(amountOf).filter((n): n is number => n !== null)
  return (
    <>
      {amounts.map((n) => (
        <button key={n} type="button" aria-label={`已核對金額 ${n.toLocaleString('en-US')}元`} onClick={() => onConfirm(n)}
          className="rounded border border-amber-400 px-2 py-0.5 text-xs text-amber-800">已核對金額</button>
      ))}
    </>
  )
}

// 起訴狀狀頭的訴訟標的金額／價額：只有非金錢請求時沒有預設值，要律師填才能匯出
export function ClaimValueField({ draft, onChanged, onError }: {
  draft: api.DraftView; onChanged: () => Promise<void>; onError: (m: string | null) => void
}) {
  const cv = draft.claim_value!
  const [value, setValue] = useState(cv.value === null ? '' : String(cv.value))
  const [busy, setBusy] = useState(false)
  const amount = Number(value)
  const valid = value.trim() !== '' && Number.isInteger(amount) && amount >= 0
  async function save() {
    setBusy(true)
    onError(null)
    try {
      await api.patchRound(draft.round_id, { claim_value: amount })
      await onChanged()
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm">
      <label className="text-slate-700">
        {cv.label}（新臺幣元）
        <input type="number" min={0} step={1} value={value} onChange={(e) => setValue(e.target.value)}
          aria-label={`${cv.label}（新臺幣元）`} className="ml-2 w-40 rounded border border-slate-300 px-2 py-1" />
      </label>
      <button type="button" disabled={busy || !valid} onClick={save} aria-label={`儲存${cv.label}`}
        className="rounded border px-3 py-1 disabled:opacity-50">儲存</button>
      <span className="text-xs text-slate-500">印在 Word 狀頭；有金錢請求時預設為請求項目加總</span>
    </div>
  )
}

export default function StatementBlock({ draft, onChanged, onError }: {
  draft: api.DraftView; onChanged: () => Promise<void>; onError: (m: string | null) => void
}) {
  const st = draft.statement!
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(st.text)
  const [busy, setBusy] = useState(false)

  async function act(f: () => Promise<unknown>) {
    setBusy(true)
    onError(null)
    try {
      await f()
    } catch (e) {
      onError(errorMessage(e))
    } finally {
      setBusy(false)
    }
    await onChanged()
  }

  const estate = draft.case_type === 'estate_partition'
  return (
    <section id="paragraph-statement" data-testid="statement-block" className="space-y-2 rounded-lg border border-indigo-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-slate-900">
          {draft.statement_label}
          {draft.statement?.source_notice && (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-normal text-amber-800">
              {draft.statement.source_notice}
            </span>
          )}
        </h2>
        <span className={`text-xs ${st.status === 'reviewed' ? 'text-emerald-700' : 'text-slate-500'}`}>
          {st.status === 'reviewed' ? `已審（${st.reviewed_bar_number ?? ''}）` : '未審'}
        </span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea aria-label="聲明內容" value={text} onChange={(e) => setText(e.target.value)} rows={6}
            className="w-full rounded border border-slate-300 p-2 font-mono text-sm" />
          <p className="text-xs text-slate-500">{'{{amount}}'} 是系統金額（已確認請求項目的加總），請勿刪除；要改金額請回到案件頁修改請求項目。</p>
          <div className="flex gap-2">
            <button type="button" disabled={busy}
              onClick={() => act(async () => { await api.patchParagraph(st.id, text, [], ''); setEditing(false) })}
              className={BTN_SECONDARY}>儲存</button>
            <button type="button" onClick={() => { setEditing(false); setText(st.text) }}
              className="rounded border px-3 py-1 text-sm">取消</button>
          </div>
        </div>
      ) : (
        <p className="font-kai whitespace-pre-wrap leading-7 text-slate-800">{st.rendered_text}</p>
      )}
      {estate && draft.brief_kind === 'answer' && (
        <p className="text-xs text-amber-700">如主張其他分割方法，請改寫聲明並附附表</p>
      )}
      {estate && draft.brief_kind === 'complaint' && <p className="text-xs text-amber-700">附表需在 Word 填寫</p>}
      {st.verify_reasons.length > 0 && (
        <ul className="text-sm text-red-700">{st.verify_reasons.map((r) => <li key={r}>{reasonLabel(r)}</li>)}</ul>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
        <button type="button" onClick={() => setEditing(true)} className="rounded border px-3 py-1 text-sm">修改</button>
        <AmountButtons reasons={st.verify_reasons} canReview={draft.can_review}
          onConfirm={(n) => act(() => api.confirmAmount(st.id, n))} />
        {draft.can_review && st.verify_reasons.includes('amounts_unchecked') && (
          <button type="button" disabled={busy} onClick={() => act(() => api.ackAmounts(draft.round_id))}
            className="rounded border border-amber-400 px-2 py-0.5 text-xs text-amber-800">金額已自行核對</button>
        )}
        {draft.can_review && (
          <button type="button" disabled={busy} onClick={() => act(() => api.reviewParagraph(st.id, st.version))}
            className="rounded bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-50">標記已審</button>
        )}
      </div>
    </section>
  )
}

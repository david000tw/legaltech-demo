// 起訴狀的我方請求項目（設計 5.2）：AI 提案，律師逐項確認金額；可修改、新增、刪除。
// 金額一旦修改就要重新確認；訴之聲明的金額由系統依確認過的項目加總，AI 不寫金額。
import { useState, type FocusEvent } from 'react'
import * as api from '../api'

const NOTE_LABEL: Record<string, string> = {
  amount_not_in_evidence: '金額未在卷證中找到',
  no_receipt: '無單據，由律師決定',
  amount_missing: '尚未填寫金額',
}
const TYPE_LABEL = { monetary: '金錢', non_monetary: '非金錢' } as const

function money(n: number | null | undefined): string {
  return n == null ? '' : `新臺幣${n.toLocaleString('en-US')}元`
}

function ItemRow({ item, canReview, act }: {
  item: api.ClaimView; canReview: boolean; act: (f: () => Promise<unknown>) => Promise<void>
}) {
  const [label, setLabel] = useState(item.summary)
  const [type, setType] = useState(item.request_type ?? 'monetary')
  const [amount, setAmount] = useState(item.amount == null ? '' : String(item.amount))
  const changed = label !== item.summary || type !== item.request_type
    || (type === 'monetary' && amount !== (item.amount == null ? '' : String(item.amount)))
  const name = `請求項目${item.seq}`
  const save = () => act(() => api.patchClaim(item.id, {
    label, request_type: type, amount: type === 'monetary' && amount !== '' ? Number(amount) : null }))
  // 改了就在離開這一列時存（例如切到卷證分頁核對原證）：分頁切走會卸載，沒存的金額不能悄悄變回 AI 的金額。
  // 同一列換欄位或按列內按鈕不算離開
  function leaveRow(e: FocusEvent<HTMLDivElement>) {
    if (changed && !e.currentTarget.contains(e.relatedTarget as Node | null)) void save()
  }

  return (
    <li className="space-y-1 py-2">
      <div className="flex flex-wrap items-center gap-2" onBlur={leaveRow}>
        <span className="text-slate-500">{item.seq}.</span>
        <input aria-label={`${name} 名稱`} value={label} onChange={(e) => setLabel(e.target.value)}
          className="w-40 rounded border border-slate-300 px-2 py-0.5" />
        <select aria-label={`${name} 種類`} value={type}
          onChange={(e) => setType(e.target.value as 'monetary' | 'non_monetary')}
          className="rounded border border-slate-300 px-1 py-0.5">
          <option value="monetary">{TYPE_LABEL.monetary}</option>
          <option value="non_monetary">{TYPE_LABEL.non_monetary}</option>
        </select>
        {type === 'monetary' && (
          <input aria-label={`${name} 金額`} inputMode="numeric" value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ''))}
            className="w-28 rounded border border-slate-300 px-2 py-0.5" placeholder="新臺幣（元）" />
        )}
        {changed && (
          <button type="button" className="rounded border px-2 py-0.5 text-xs" onClick={() => void save()}>
            儲存
          </button>
        )}
        <span className={item.confirmed ? 'text-xs text-emerald-700' : 'text-xs text-amber-700'}>
          {item.confirmed ? '已確認' : '待確認'}
        </span>
        {canReview && !item.confirmed && !changed && (
          <button type="button" aria-label={`確認 ${name}`} onClick={() => act(() => api.confirmClaim(item.id))}
            className="rounded bg-emerald-600 px-2 py-0.5 text-xs text-white">確認</button>
        )}
        <button type="button" aria-label={`刪除 ${name}`} onClick={() => act(() => api.deleteClaim(item.id))}
          className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-700">刪除</button>
      </div>
      {item.amount_note && <p className="ml-5 text-xs text-amber-700">{NOTE_LABEL[item.amount_note]}</p>}
      {(item.evidence ?? []).map((e, i) => (
        <p key={i} className="ml-5 text-xs text-slate-600">
          {e.found
            ? <span className="text-slate-700">{`原文出處：${e.exhibit} 第${e.page_no}頁`}</span>
            : <span className="text-red-700">未找到原文</span>}
          ：「{e.quote}」
        </p>
      ))}
    </li>
  )
}

export default function RequestItemsEditor({ round, onChanged, onError }: {
  round: api.RoundDetail; onChanged: () => Promise<void>; onError: (m: string | null) => void
}) {
  const [label, setLabel] = useState('')
  const [type, setType] = useState<'monetary' | 'non_monetary'>('monetary')
  const [amount, setAmount] = useState('')
  const items = round.claims.filter((c) => c.kind === 'request')

  async function act(f: () => Promise<unknown>) {
    onError(null)
    try {
      await f()
    } catch (e) {
      onError((e as Error).message)
    }
    await onChanged()
  }

  const add = () => act(async () => {
    await api.addRequest(round.id, { label, request_type: type,
      amount: type === 'monetary' && amount !== '' ? Number(amount) : null })
    setLabel('')
    setAmount('')
  })

  return (
    <div className="mt-3 space-y-2 text-sm">
      <h4 className="font-medium text-slate-800">我方請求項目</h4>
      <ul className="divide-y divide-slate-100">
        {items.map((it) => (
          <ItemRow key={`${it.id}-${it.amount}-${it.summary}-${it.confirmed}`} item={it} canReview={!!round.can_review}
            act={act} />
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="新增請求項目 名稱" value={label} onChange={(e) => setLabel(e.target.value)}
          placeholder="例如：交通費" className="w-40 rounded border border-slate-300 px-2 py-0.5" />
        <select aria-label="新增請求項目 種類" value={type}
          onChange={(e) => setType(e.target.value as 'monetary' | 'non_monetary')}
          className="rounded border border-slate-300 px-1 py-0.5">
          <option value="monetary">{TYPE_LABEL.monetary}</option>
          <option value="non_monetary">{TYPE_LABEL.non_monetary}</option>
        </select>
        {type === 'monetary' && (
          <input aria-label="新增請求項目 金額" inputMode="numeric" value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ''))}
            className="w-28 rounded border border-slate-300 px-2 py-0.5" placeholder="新臺幣（元）" />
        )}
        <button type="button" disabled={!label.trim()} onClick={add}
          className="rounded border px-2 py-0.5 text-xs disabled:opacity-50">新增</button>
      </div>
      <p className="font-medium text-slate-800">{`合計${money(round.total_amount ?? 0)}`}</p>
      {round.statement_preview && (
        <div className="rounded bg-slate-50 p-2">
          <p className="text-xs text-slate-500">訴之聲明預覽（金額由系統依已確認的項目加總）</p>
          <p className="whitespace-pre-wrap text-slate-800">{round.statement_preview}</p>
        </div>
      )}
      {(round.pending_ocr_pages ?? []).length > 0 && (
        <p className="text-xs text-amber-700">{`待辨識，未處理：${(round.pending_ocr_pages ?? []).join('、')}`}</p>
      )}
    </div>
  )
}

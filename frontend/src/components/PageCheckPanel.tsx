// 對照原圖面板（計畫 2A，設計 6.5）：左邊原圖、右邊辨識文字；提醒排最前面，點一則就在文字反白、在圖上畫框。
// 律師對照原圖後勾「已對照原圖」；修正文字會產生新版本，要重新對照。
import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import { confirmOcr } from '../ocrConfirm'
import { BTN_SECONDARY, BTN_SMALL } from '../ui'

const ALERT_STYLE = {
  red: 'border-red-300 bg-red-50 text-red-800',
  yellow: 'border-amber-300 bg-amber-50 text-amber-800',
}

export default function PageCheckPanel({ pageId, canReview, onChanged }: {
  pageId: string
  canReview: boolean
  onChanged?: () => void | Promise<void>
}) {
  const [data, setData] = useState<api.PageOcr | null>(null)
  const [text, setText] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)
  const [active, setActive] = useState<api.OcrAlert | null>(null)
  const [busy, setBusy] = useState(false)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)  // 原圖實際像素（框的座標以它為準）
  const boxRef = useRef<HTMLTextAreaElement>(null)

  const show = useCallback((d: api.PageOcr) => {
    setData(d)
    setText(d.text ?? '')
    setActive(null)
  }, [])
  const load = useCallback(async () => show(await api.getPageOcr(pageId)), [pageId, show])

  useEffect(() => {
    load().catch((e: Error) => setMessage(e.message))
  }, [load])

  async function run(action: () => Promise<api.PageOcr | null>) {
    setBusy(true)
    setMessage(null)
    try {
      const d = await action()
      if (d) show(d)
      await onChanged?.()
    } catch (e) {
      setMessage((e as Error).message)
      if (e instanceof api.ApiError && e.status === 409) await load().catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  function pick(a: api.OcrAlert) {
    setActive(a)
    const box = boxRef.current
    if (box) {
      box.focus()
      box.setSelectionRange(a.start, a.end)
    }
  }

  if (!data) return <p className="text-sm text-slate-600">{message ?? '載入中…'}</p>
  const alerts = [...data.alerts].sort((a, b) => (a.color === 'red' ? 0 : 1) - (b.color === 'red' ? 0 : 1))
  const dirty = text !== (data.text ?? '')
  const box = active?.box
  // 以原圖像素換算成百分比，放大縮小時框跟著圖走；圖還沒載入時先用像素
  const pos = (v: number, total: number | undefined) => (total ? `${(v / total) * 100}%` : v)

  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold text-slate-800">{data.label}</h3>
        {data.verified && <span className="text-xs text-emerald-700">{`${data.verified.by_name ?? ''} 已對照原圖`}</span>}
        {data.needs_check && <span className="text-xs text-amber-700">辨識文字，未對照原圖</span>}
      </div>
      {data.ocr_state === 'failed' && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          <span>{data.ocr_error}</span>
          <button type="button" disabled={busy} className={BTN_SMALL}
            onClick={() => run(async () => {
              await confirmOcr((c) => api.startDocumentOcr(data.document_id, c ? { confirm: true, retryFailed: true }
                : { retryFailed: true }))
              return api.getPageOcr(pageId)
            })}>重新辨識</button>
          <span>也可以直接在右邊手動輸入</span>
        </div>
      )}
      {alerts.length > 0 && (
        <ul className="space-y-1">
          {alerts.map((a, i) => (
            <li key={`${a.kind}-${a.start}-${i}`}>
              <button type="button" onClick={() => pick(a)}
                className={`w-full rounded border px-2 py-1 text-left text-xs ${ALERT_STYLE[a.color]}`}>
                {a.message}
                {!data.needs_check && data.verified && <span className="ml-2 text-emerald-700">已對照</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-1">
          <div className="flex gap-2">
            <button type="button" className={BTN_SMALL} onClick={() => setZoom((z) => Math.min(z * 1.25, 4))}>放大</button>
            <button type="button" className={BTN_SMALL} onClick={() => setZoom((z) => Math.max(z / 1.25, 0.5))}>縮小</button>
          </div>
          {data.has_image ? (
            <div className="max-h-[70vh] overflow-auto rounded border border-slate-200">
              <div className="relative" style={{ width: `${zoom * 100}%` }}>
                <img src={api.pageImageUrl(pageId)} alt={`${data.label ?? ''} 原圖`} className="block w-full"
                  onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
                {box && (
                  <div data-testid="alert-box" className="pointer-events-none absolute border-2 border-red-500"
                    style={{ left: pos(box[0], natural?.w), top: pos(box[1], natural?.h),
                      width: pos(box[2] - box[0], natural?.w), height: pos(box[3] - box[1], natural?.h) }} />
                )}
              </div>
            </div>
          ) : <p className="text-xs text-slate-500">這一頁沒有原圖（Word 或純文字檔）</p>}
        </div>
        <textarea ref={boxRef} aria-label="辨識文字" value={text} readOnly={!data.can_edit}
          onChange={(e) => setText(e.target.value)} rows={18}
          className="w-full rounded border border-slate-300 p-2 font-mono text-xs" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {data.can_edit && (
          <button type="button" disabled={busy} className={BTN_SECONDARY}
            onClick={() => run(() => api.savePageText(pageId, text, data.version, false))}>儲存</button>
        )}
        {canReview && data.can_edit && (
          <button type="button" disabled={busy || !data.can_verify} className={BTN_SECONDARY}
            onClick={() => run(() => api.savePageText(pageId, text, data.version, true))}>儲存並標為已對照</button>
        )}
        {canReview && (
          // 有未儲存的修改時不能只勾已對照：那會把舊文字標成已對照，修改也會被丟掉
          <button type="button" disabled={busy || !data.can_verify || data.version === null || dirty}
            className={BTN_SECONDARY}
            onClick={() => run(() => api.verifyPage(pageId, data.version as number))}>已對照原圖</button>
        )}
        {canReview && dirty && <span className="text-xs text-amber-700">有未儲存的修改：請按「儲存並標為已對照」</span>}
        {data.blank_unconfirmed && (
          <button type="button" disabled={busy} className={BTN_SECONDARY}
            onClick={() => run(() => api.confirmBlank(pageId))}>確認是空白頁</button>
        )}
      </div>
      {message && <p role="alert" className="text-sm text-red-700">{message}</p>}
    </div>
  )
}

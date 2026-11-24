// 一份文件的辨識狀態（計畫 2A）：進度、每頁一個小標籤（點了開啟對照原圖面板），以及開始、繼續、重新辨識、改用辨識。
import { useState } from 'react'
import * as api from '../api'
import { POLL_MS, useJobPolling } from '../aiJobs'
import { ocrStateLabel } from '../labels'
import { confirmOcr } from '../ocrConfirm'
import { BTN_SMALL } from '../ui'

const TAG: Record<string, string> = {
  red: 'border-red-300 bg-red-50 text-red-800',
  amber: 'border-amber-300 bg-amber-50 text-amber-800',
  green: 'border-emerald-300 bg-emerald-50 text-emerald-800',
  gray: 'border-slate-300 bg-slate-50 text-slate-700',
}

// 辨識整份停下的原因（設計 6.2「額度用完…顯示原因」）：兩種要律師處理的給白話說明，其他照原文
function ocrStopReason(error: string): string {
  if (error.startsWith('usage_limit')) {
    return '辨識已停下：Claude 用量額度已用完。額度恢復後按「繼續辨識」接著做；已辨識的頁照常可以對照。'
  }
  if (error.startsWith('not_logged_in')) return '辨識已停下：本機 Claude Code 尚未登入。請先登入，再按「繼續辨識」。'
  return `辨識已停下：${error.replace(/^[a-z_]+: /, '')}`
}

function tone(page: api.DocPage): string {
  if (page.ocr_state === 'failed') return TAG.red
  if (page.ocr_state === 'done') return page.needs_check ? TAG.amber : TAG.green
  return TAG.gray
}

export default function OcrStatus({ doc, onChanged, onOpenPage, pollMs = POLL_MS }: {
  doc: api.CaseDocument
  onChanged: () => Promise<void>
  onOpenPage: (pageId: string) => void
  pollMs?: number
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ocr = doc.ocr
  const job = ocr?.job ?? null
  const running = job?.status === 'running' && !job.stale
  useJobPolling(running, onChanged, pollMs)
  const pages = doc.pages.filter((p) => (p.ocr_state ?? 'none') !== 'none')
  if (!ocr || (pages.length === 0 && !ocr.can_reocr)) return null
  const p = ocr.progress
  const waiting = job?.status === 'waiting'

  async function act<T>(start: (confirm: boolean) => Promise<T>) {
    setBusy(true)
    setError(null)
    try {
      if ((await confirmOcr(start)) !== null) await onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-1 space-y-1 text-xs">
      {running && (
        <p className="text-slate-700">{`辨識中 第 ${p.done + p.failed}／${p.total} 頁，約剩 ${p.minutes_left} 分鐘`}</p>
      )}
      {pages.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {pages.map((pg) => {
            const label = ocrStateLabel(pg, job)
            return label && (
              <button key={pg.id} type="button" onClick={() => onOpenPage(pg.id)} aria-label={`第${pg.page_no}頁：${label}`}
                className={`rounded border px-1.5 py-0.5 ${tone(pg)}`}>
                <span>{`第${pg.page_no}頁`}</span> <span>{label}</span>
              </button>
            )
          })}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {waiting && (
          <button type="button" disabled={busy} className={BTN_SMALL}
            onClick={() => act((c) => api.startDocumentOcr(doc.id, c ? { confirm: true } : {}))}>
            {`開始辨識（${p.total - p.done - p.failed} 頁）`}
          </button>
        )}
        {ocr.can_resume && (
          <button type="button" disabled={busy} className={BTN_SMALL}
            onClick={() => act((c) => api.startDocumentOcr(doc.id, c ? { confirm: true } : {}))}>繼續辨識</button>
        )}
        {p.failed > 0 && !running && !waiting && (
          <button type="button" disabled={busy} className={BTN_SMALL}
            onClick={() => act((c) => api.startDocumentOcr(doc.id, c ? { confirm: true, retryFailed: true }
              : { retryFailed: true }))}>重新辨識</button>
        )}
        {ocr.can_reocr && (
          <button type="button" disabled={busy || running} className={BTN_SMALL}
            onClick={() => act((c) => api.reocrDocument(doc.id, c))}>改用辨識</button>
        )}
        {ocr.can_reocr && ocr.scan_hint && (
          <span className="text-amber-700">這份看起來是掃描後加上文字的 PDF，建議改用辨識</span>
        )}
      </div>
      {job?.status === 'failed' && job.error && !error && (
        <p role="alert" className="text-red-700">{ocrStopReason(job.error)}</p>
      )}
      {error && <p role="alert" className="text-red-700">{error}</p>}
    </div>
  )
}

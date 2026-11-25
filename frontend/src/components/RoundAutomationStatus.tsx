import * as api from '../api'

// 輪次卡片上的自動流程狀態（設計 6.2）：正在跑哪一步，或在等什麼、怎麼處理。「每一步我自己按」時不顯示。
const RUNNING: Record<'extract' | 'draft', string> = { extract: '自動處理中：拆解中', draft: '自動處理中：起草中（約 4 分鐘）' }
const OCR_EXIT: Record<'retry_failed' | 'confirm_ocr' | 'resume_ocr', [string, { confirm?: boolean; retryFailed?: boolean }]> = {
  retry_failed: ['重新辨識失敗的頁', { retryFailed: true }],
  confirm_ocr: ['確認辨識', { confirm: true }],
  resume_ocr: ['繼續辨識', {}],
}
const BTN = 'rounded border border-amber-400 px-2 py-0.5 text-xs text-amber-800'

export default function RoundAutomationStatus({ roundId, automation, onAction, onOpenPage }: {
  roundId: string
  automation: api.AutomationView
  onAction: (f: () => Promise<unknown>) => Promise<void>
  onOpenPage: (pageId: string) => void
}) {
  if (automation.mode === 'manual') return null
  const { running, wait } = automation
  if (running) return <p className="mt-2 text-sm text-sky-700">{RUNNING[running]}</p>
  if (!wait) return null
  const docId = wait.document_id
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded bg-amber-50 px-2 py-1 text-sm text-amber-900">
      <span>{`等待中：${wait.message}`}</span>
      {wait.code === 'opponent_ocr' && wait.page_id && (
        <button type="button" className={BTN} onClick={() => onOpenPage(wait.page_id as string)}>看那一頁</button>
      )}
      {wait.code === 'evidence_notes_running' && (
        <button type="button" className={BTN} onClick={() => void onAction(() => api.draftRound(roundId, { force: true }))}>
          先起草（不等卷證說明）
        </button>
      )}
      {wait.code === 'evidence_ocr' && docId && wait.hint && wait.hint !== 'start_notes' && (
        <button type="button" className={BTN}
          onClick={() => void onAction(() => api.startDocumentOcr(docId, OCR_EXIT[wait.hint as keyof typeof OCR_EXIT][1]))}>
          {OCR_EXIT[wait.hint as keyof typeof OCR_EXIT][0]}
        </button>
      )}
      {wait.code === 'evidence_ocr' && (
        <button type="button" className={BTN} onClick={() => void onAction(() => api.draftRound(roundId, { force: true }))}>
          先起草（不含還沒辨識的頁）
        </button>
      )}
      {wait.code === 'new_brief' && (
        <>
          <button type="button" className={BTN} onClick={() => void onAction(async () => {
            await api.resetRound(roundId, true)
            await api.extractRound(roundId)
          })}>重新拆解</button>
          <button type="button" className={BTN}
            onClick={() => void onAction(() => api.draftRound(roundId, { ignoreNewBriefs: true }))}>
            不納入，直接起草
          </button>
        </>
      )}
    </div>
  )
}

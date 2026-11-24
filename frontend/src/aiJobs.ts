// 按鈕觸發的 AI 工作（卷證說明、大事記、健檢）與輪次的拆解、起草共用：輪詢間隔、輪詢 hook、處理中的提示。
import { useEffect } from 'react'

export const POLL_MS = 3000

export type AiStep = 'extract' | 'draft' | 'evidence_notes' | 'timeline' | 'health'
const MINUTES: Record<AiStep, number> = { extract: 1, draft: 4, evidence_notes: 1, timeline: 1, health: 2 }

// 畫面上的「處理中」提示，附大約需要的時間（後端忙碌時的 409 訊息不變）
export const busyText = (step: AiStep) => `AI 處理中，約需 ${MINUTES[step]} 分鐘`

// running 時每隔 pollMs 重新載入一次，直到工作結束
export function useJobPolling(running: boolean, refresh: () => Promise<void>, pollMs: number = POLL_MS) {
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => { void refresh() }, pollMs)
    return () => clearInterval(t)
  }, [running, refresh, pollMs])
}

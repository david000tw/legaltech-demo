// 按鈕觸發的 AI 工作（卷證說明、大事記、健檢）共用：輪詢間隔與輪詢 hook。
import { useEffect } from 'react'

export const POLL_MS = 3000
export const BUSY_TEXT = 'AI 處理中，請稍候'

// running 時每隔 pollMs 重新載入一次，直到工作結束
export function useJobPolling(running: boolean, refresh: () => Promise<void>, pollMs: number = POLL_MS) {
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => { void refresh() }, pollMs)
    return () => clearInterval(t)
  }, [running, refresh, pollMs])
}

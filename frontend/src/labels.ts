import type { BriefKind, DocPage, OcrJob } from './api'

export const KIND_LABEL: Record<BriefKind, string> = { complaint: '起訴狀', answer: '答辯狀', preparatory: '準備書狀' }

// 頁的辨識狀態標籤（計畫 2A）；檔案本身有文字的頁（none）不顯示
export function ocrStateLabel(page: DocPage, job: OcrJob | null | undefined): string | null {
  const state = page.ocr_state ?? 'none'
  if (state === 'none') return null
  if (job?.status === 'waiting' && state === 'pending') return '等待確認'
  const running = job?.status === 'running' && !job.stale
  if (state === 'pending') return running ? '辨識中' : '待辨識'
  if (state === 'read') return '校對中'
  if (state === 'failed') return '辨識失敗'
  return page.needs_check ? `已辨識（${page.alert_count ?? 0} 則提醒）` : '已對照原圖'
}

// 掃描頁的上傳說明（卷證、對造書狀共用）
export const UPLOAD_HINT = '可上傳 PDF、Word、純文字、JPG、PNG；iPhone 的 HEIC 照片請先轉成 JPG'
export const UPLOAD_ACCEPT = '.pdf,.docx,.txt,.jpg,.jpeg,.png'

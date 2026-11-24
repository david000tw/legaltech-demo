// 開始辨識類的請求：頁數超過上限時後端回 409 confirm_ocr，先問律師，同意後帶 confirm 再送一次（計畫 2A）。
import { ApiError } from './api'

export async function confirmOcr<T>(start: (confirm: boolean) => Promise<T>): Promise<T | null> {
  try {
    return await start(false)
  } catch (e) {
    if (e instanceof ApiError && e.code === 'confirm_ocr') {
      if (!window.confirm(e.message)) return null
      return await start(true)
    }
    throw e
  }
}

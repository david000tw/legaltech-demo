import { describe, expect, it } from 'vitest'
import { reasonLabel } from './ExportButton'

describe('ExportButton reason labels', () => {
  it('labels the new blocker reasons including the amount page', () => {
    expect(reasonLabel('image_unverified:E2')).toBe('E2 的頁尚未對照原圖')
    expect(reasonLabel('quote_unreadable')).toBe('引文含看不清楚的字，請先修正辨識文字')
    expect(reasonLabel('inline_quote_image_unverified:原告行經無號誌路口時'))
      .toBe('引文「原告行經無號誌路口時…」出自未對照原圖的對造書狀頁')
    expect(reasonLabel('amount_image_unverified:148000:原證1 第1頁'))
      .toBe('金額 148,000 出自未對照原圖的辨識文字（原證1 第1頁）')
    expect(reasonLabel('amount_image_unverified:5000:對造書狀「答辯狀.pdf」第2頁'))
      .toBe('金額 5,000 出自未對照原圖的辨識文字（對造書狀「答辯狀.pdf」第2頁）')
  })

  it('labels the support check reasons', () => {
    expect(reasonLabel('support_pending:J1')).toBe('J1 支持度檢查中')
    expect(reasonLabel('support_unchecked:E1')).toBe('E1 支持度尚未檢查，請重新檢查或填理由放行')
    expect(reasonLabel('support_not_supported:J1')).toBe('J1 經 AI 判斷不支持這句話，請修改或填理由放行')
    expect(reasonLabel('support_misuse:J1')).toBe('J1 經 AI 判斷是把當事人主張當成法院見解，請修改或填理由放行')
    expect(reasonLabel('support_partial:E1')).toBe('E1 只部分支持這句話，請確認後放行')
  })

  it('labels the same-code different-quotes conflict', () => {
    expect(reasonLabel('citation_quote_conflict:J1')).toBe('同一判決引文不一致：J1 在同一段出現了兩段不同的引文，系統只保留第一段，請改寫或拆開')
    expect(reasonLabel('quote_conflict')).toBe('同一代碼出現了不同的引文')
  })
})

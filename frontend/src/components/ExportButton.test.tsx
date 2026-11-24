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
})

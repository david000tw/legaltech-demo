import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ExportButton, { reasonLabel } from './ExportButton'

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

  it('labels reasons that the paragraph review now settles', () => {
    expect(reasonLabel('judgment_not_opened:J1')).toBe('還要先看原文（J1）')
    expect(reasonLabel('judgment_unconfirmed:J1')).toBe('J1：打開原文後，按該段「已審」一併確認')
    expect(reasonLabel('support_partial:E1')).toBe('E1 部分支持：看過說明後，按該段「已審」一併接受')
  })

  it('labels the support check reasons', () => {
    expect(reasonLabel('support_pending:J1')).toBe('J1 支持度檢查中')
    expect(reasonLabel('support_unchecked:E1')).toBe('E1 支持度尚未檢查，請重新檢查或填理由放行')
    expect(reasonLabel('support_not_supported:J1')).toBe('J1 經 AI 判斷不支持這句話，請修改或填理由放行')
    expect(reasonLabel('support_misuse:J1')).toBe('J1 經 AI 判斷是把當事人主張當成法院見解，請修改或填理由放行')
    expect(reasonLabel('support_partial:E1')).toBe('E1 部分支持：看過說明後，按該段「已審」一併接受')
  })

  it('labels the same-code different-quotes conflict', () => {
    expect(reasonLabel('citation_quote_conflict:J1')).toBe('同一引用的引文不一致：J1 在同一段出現了兩段不同的引文，系統只保留第一段，請改寫或拆開')
    expect(reasonLabel('quote_conflict')).toBe('同一代碼出現了不同的引文')
    expect(reasonLabel('reviewed_text_changed')).toBe('審核後顯示內容有變更（例如系統調整引用格式），已退回未審，請重新檢視後標記已審')
  })
})

describe('ExportButton blocker list', () => {
  const blockers = [
    { paragraph_seq: 2, paragraph_no: 2, reason: 'unreviewed', scope: 'paragraph' as const },
    { paragraph_seq: 5, paragraph_no: 5, reason: 'unreviewed', scope: 'paragraph' as const },
    { paragraph_seq: 0, paragraph_no: 0, reason: 'empty_draft', scope: 'draft' as const },
  ]

  it('starts collapsed with a one-line summary', () => {
    render(<ExportButton blockers={blockers} onExport={() => {}} />)
    expect(screen.getByText('還有 3 項要處理：整份書狀、第 2、5 段')).toBeInTheDocument()
    expect(screen.queryByText(/段落尚未審核/)).not.toBeInTheDocument()
  })

  it('expands grouped by place with draft-level items first, and jumps on click', async () => {
    const onJump = vi.fn()
    render(<ExportButton blockers={blockers} onExport={() => {}} onJump={onJump} />)
    await userEvent.click(screen.getByRole('button', { name: '展開' }))
    const items = screen.getAllByRole('button', { name: /：/ })
    expect(items.map((b) => b.textContent)).toEqual(['整份書狀：草稿沒有任何段落', '第 2 段：段落尚未審核', '第 5 段：段落尚未審核'])
    await userEvent.click(items[1])
    expect(onJump).toHaveBeenCalledWith(blockers[0])
  })
})

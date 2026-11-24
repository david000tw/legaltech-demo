import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import RequestItemsEditor from './RequestItemsEditor'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, confirmClaim: vi.fn(), patchClaim: vi.fn(), deleteClaim: vi.fn(), addRequest: vi.fn() }
})

const round: api.RoundDetail = { id: 'r1', seq: 1, status: 'extracted', error: null, stale: false,
  brief_kind: 'complaint', failed_stage: null, draft_id: null, can_review: true, total_amount: 0,
  claims: [{ id: 'q1', seq: 1, kind: 'request', selected: true, summary: '醫療費用', quote: '醫療費用合計新臺幣45,000元',
    quote_verified: true, page_no: 1, request_type: 'monetary', amount: 45000, amount_note: null, basis_text: null,
    confirmed: false, page_id: 'p1', ocr_unverified: true,
    evidence: [{ exhibit: '原證1', page_no: 1, quote: '醫療費用合計新臺幣45,000元', found: true, page_id: 'p1',
      ocr_unverified: true }] }] }

beforeEach(() => vi.resetAllMocks())

describe('RequestItemsEditor', () => {
  it('request evidence needs image check before confirm', async () => {
    const onOpenPage = vi.fn()
    const onError = vi.fn()
    vi.mocked(api.confirmClaim).mockRejectedValue(
      new api.ApiError(409, '請先對照原圖：原證1 第1頁', 'image_unverified'))
    render(<RequestItemsEditor round={round} onChanged={vi.fn(async () => {})} onError={onError}
      onOpenPage={onOpenPage} />)
    expect(screen.getByText('辨識文字，未對照原圖')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '對照原圖' }))
    expect(onOpenPage).toHaveBeenCalledWith('p1')
    onOpenPage.mockClear()
    await userEvent.click(screen.getByRole('button', { name: '確認 請求項目1' }))
    await waitFor(() => expect(onError).toHaveBeenCalledWith('請先對照原圖：原證1 第1頁'))
    expect(onOpenPage).toHaveBeenCalledWith('p1')
  })
})

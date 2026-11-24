import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { SourceView } from '../api'
import SourceBadge from './SourceBadge'

const src = (over: Partial<SourceView>): SourceView => ({
  page_id: 'p1', label: '原證3 第1頁', quote: '支付醫療費用新臺幣45,000元', status: 'found', reasons: [], ...over,
})

describe('SourceBadge', () => {
  it('shows found / not found / absence with their colours', () => {
    const { container, unmount } = render(<SourceBadge source={src({})} origin="ai" lawyerLabel="律師修改" />)
    expect(screen.getByText('原文出處：原證3 第1頁').className).toMatch(/text-slate-700/)
    expect(screen.getByText('「支付醫療費用新臺幣45,000元」')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/已驗證|已證實/)
    unmount()
    const nf = render(<SourceBadge source={src({ status: 'not_found', reasons: ['quote_not_found'], label: null })}
      origin="ai" lawyerLabel="律師修改" />)
    expect(screen.getByText('未找到原文').className).toMatch(/text-red-700/)
    nf.unmount()
    render(<SourceBadge source={src({ status: 'absence' })} origin="ai" lawyerLabel="律師修改" />)
    expect(screen.getByText('缺漏類，系統無法比對，請律師判斷').className).toMatch(/text-slate-400/)
  })

  it('lists every failed check for timeline events', () => {
    render(<SourceBadge source={src({ status: 'not_found', reasons: ['date_not_in_quote', 'amount_not_in_quote'] })}
      origin="ai" lawyerLabel="律師" />)
    expect(screen.getByText('日期不在原文摘錄裡、金額不在原文摘錄裡').className).toMatch(/text-red-700/)
  })

  it('shows lawyer label and plain location for lawyer rows', () => {
    const { container } = render(<SourceBadge source={src({ status: 'unchecked' })} origin="lawyer" lawyerLabel="律師修改" />)
    expect(screen.getByText('律師修改')).toBeInTheDocument()
    expect(screen.getByText('出處：原證3 第1頁')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/原文出處/)
  })

  it('opens the source when clickable', async () => {
    const onShow = vi.fn()
    render(<SourceBadge source={src({})} origin="ai" lawyerLabel="律師修改" onShow={onShow} />)
    await userEvent.click(screen.getByRole('button', { name: '原文出處：原證3 第1頁' }))
    expect(onShow).toHaveBeenCalledWith(expect.objectContaining({ page_id: 'p1' }))
  })

  it('renders nothing for a lawyer row without a source', () => {
    render(<SourceBadge source={null} origin="lawyer" lawyerLabel="律師" />)
    expect(screen.getByText('律師')).toBeInTheDocument()
    expect(screen.queryByText(/出處/)).not.toBeInTheDocument()
  })
})

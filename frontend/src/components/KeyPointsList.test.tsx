import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type * as api from '../api'
import KeyPointsList from './KeyPointsList'

const data: api.KeyPoints = {
  legacy: false, complaint: false,
  items: [
    { seq: 1, summary: '被告未讓幹道車先行', selected: true, uncovered: false,
      paragraphs: [{ seq: 2, no: 2, heading: '被告已減速，原告亦與有過失' }] },
    { seq: 2, summary: '事故發生在5月2日', selected: true, uncovered: true, paragraphs: [] },
    { seq: 3, summary: '精神慰撫金8萬元', selected: false, uncovered: false, paragraphs: [] },
  ],
}

describe('KeyPointsList', () => {
  it('lists each opponent point with the paragraph that answers it', async () => {
    const onJump = vi.fn()
    render(<KeyPointsList data={data} onJump={onJump} />)
    expect(screen.getByText('這份草稿回應了對方 2 個重點（對應由 AI 標註）')).toBeInTheDocument()
    expect(screen.getByText('被告未讓幹道車先行')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '第二段：被告已減速，原告亦與有過失' }))
    expect(onJump).toHaveBeenCalledWith(2)
    expect(screen.getByText('⚠ 沒有段落回應')).toBeInTheDocument()
    expect(screen.getByText('未勾選，不回應')).toBeInTheDocument()
  })

  it('explains an old draft without the mapping', () => {
    render(<KeyPointsList data={{ ...data, legacy: true }} onJump={vi.fn()} />)
    expect(screen.getByText('這份草稿是舊版產生的，沒有對應資料')).toBeInTheDocument()
    expect(screen.queryByText('⚠ 沒有段落回應')).not.toBeInTheDocument()
  })

  it('says request items for a complaint', () => {
    render(<KeyPointsList data={{ ...data, complaint: true }} onJump={vi.fn()} />)
    expect(screen.getByText('這份草稿涵蓋的請求項目（對應由 AI 標註）')).toBeInTheDocument()
  })
})

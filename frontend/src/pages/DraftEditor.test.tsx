import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import DraftEditor from './DraftEditor'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getDraft: vi.fn(), getCitationSource: vi.fn(), confirmCitation: vi.fn(), reviewParagraph: vi.fn(),
    patchParagraph: vi.fn(), rewriteParagraph: vi.fn(), exportDraft: vi.fn() }
})

const draft: api.DraftView = {
  id: 'dr1', round_id: 'r1', case_id: 'c1',
  blockers: [{ paragraph_seq: 1, reason: 'unreviewed' }, { paragraph_seq: 1, reason: 'judgment_unconfirmed:J1' }],
  paragraphs: [{
    id: 'p1', seq: 1, heading: '㈠關於路權', text: '按{{cite:J1}}認被告{{quote:J1}}。',
    rendered_text: '按臺灣臺中地方法院113年度中簡字第3946號民事判決認被告「未在適當位置暫停禮讓幹線道之系爭車輛」。',
    status: 'unreviewed', verify_reasons: [], reviewed_bar_number: null, reviewed_at: null,
    citations: [{ id: 'cj1', code: 'J1', kind: 'judgment', quote: '未在適當位置暫停禮讓幹線道之系爭車輛',
      label: '臺灣臺中地方法院113年度中簡字第3946號民事判決', verify_status: 'ok', verify_reasons: [],
      lawyer_confirmed: false }],
  }],
}

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/drafts/dr1']}>
      <Routes><Route path="/drafts/:draftId" element={<DraftEditor />} /></Routes>
    </MemoryRouter>,
  )
}

describe('DraftEditor', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.getDraft).mockResolvedValue(draft)
  })

  it('shows rendered paragraphs and the source of a clicked citation', async () => {
    vi.mocked(api.getCitationSource).mockResolvedValue({ kind: 'judgment', title: '臺中地院（法院判斷段）',
      text: '被告未在適當位置暫停禮讓幹線道之系爭車輛，有過失。', span: [2, 20] })
    renderEditor()
    expect(await screen.findByText(/未在適當位置暫停禮讓幹線道之系爭車輛」。/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /J1/ }))
    const pane = await screen.findByTestId('source-pane')
    expect(within(pane).getByText('臺中地院（法院判斷段）')).toBeInTheDocument()
    expect(pane.querySelector('mark')?.textContent).toBe('未在適當位置暫停禮讓幹線道之系爭車輛')
    expect(api.getCitationSource).toHaveBeenCalledWith('cj1')
  })

  it('confirms a judgment citation and marks the paragraph reviewed', async () => {
    vi.mocked(api.confirmCitation).mockResolvedValue({ ...draft.paragraphs[0].citations[0], lawyer_confirmed: true })
    vi.mocked(api.reviewParagraph).mockResolvedValue({ ...draft.paragraphs[0], status: 'reviewed' })
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: '已讀原文，確認' }))
    expect(api.confirmCitation).toHaveBeenCalledWith('cj1')
    await userEvent.click(screen.getByRole('button', { name: '標記已審' }))
    expect(api.reviewParagraph).toHaveBeenCalledWith('p1')
    expect(api.getDraft).toHaveBeenCalledTimes(3)
  })

  it('saves an edit with the existing citations', async () => {
    vi.mocked(api.patchParagraph).mockResolvedValue(draft.paragraphs[0])
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: '修改' }))
    const box = screen.getByRole('textbox', { name: '段落內文' })
    await userEvent.clear(box)
    await userEvent.type(box, '新內文{{{{cite:J1}}')
    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(api.patchParagraph).toHaveBeenCalledWith('p1', '新內文{{cite:J1}}',
      [{ code: 'J1', quote: '未在適當位置暫停禮讓幹線道之系爭車輛' }])
  })

  it('export button lists blockers', async () => {
    renderEditor()
    expect(await screen.findByText(/判決引用尚未確認（J1）/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '匯出 Word' })).toBeDisabled()
  })
})

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import DraftEditor from './DraftEditor'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getDraft: vi.fn(), getCitationSource: vi.fn(), confirmCitation: vi.fn(), confirmCourt: vi.fn(), reviewParagraph: vi.fn(),
    patchParagraph: vi.fn(), rewriteParagraph: vi.fn(), exportDraft: vi.fn() }
})

const draft: api.DraftView = {
  id: 'dr1', round_id: 'r1', case_id: 'c1', can_review: true,
  blockers: [{ paragraph_seq: 1, reason: 'unreviewed' }, { paragraph_seq: 1, reason: 'judgment_unconfirmed:J1' }],
  paragraphs: [{
    id: 'p1', seq: 1, heading: '㈠關於路權', text: '按{{cite:J1}}認被告{{quote:J1}}。',
    rendered_text: '按臺灣臺中地方法院113年度中簡字第3946號民事判決認被告「未在適當位置暫停禮讓幹線道之系爭車輛」。',
    version: 'v1', status: 'unreviewed', verify_reasons: [], reviewed_bar_number: null, reviewed_at: null,
    citations: [{ id: 'cj1', code: 'J1', kind: 'judgment', quote: '未在適當位置暫停禮讓幹線道之系爭車輛',
      label: '臺灣臺中地方法院113年度中簡字第3946號民事判決', verify_status: 'ok', verify_reasons: [],
      lawyer_confirmed: false, court_confirmed: false }],
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
    expect(api.reviewParagraph).toHaveBeenCalledWith('p1', 'v1')
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
      [{ code: 'J1', quote: '未在適當位置暫停禮讓幹線道之系爭車輛' }], '㈠關於路權')
  })

  it('export button lists blockers', async () => {
    renderEditor()
    expect(await screen.findByText(/判決引用尚未確認（J1）/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '匯出 Word' })).toBeDisabled()
  })

  it('hides lawyer-only actions from assistants', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, can_review: false })
    renderEditor()
    expect(await screen.findByRole('button', { name: '修改' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '已讀原文，確認' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '標記已審' })).not.toBeInTheDocument()
  })

  it('shows a conflict, reloads, and clears the message after the next success', async () => {
    vi.mocked(api.reviewParagraph).mockRejectedValue(new api.ApiError(409, '段落內容已變更，請重新載入後再審核'))
    vi.mocked(api.confirmCitation).mockResolvedValue({ ...draft.paragraphs[0].citations[0], lawyer_confirmed: true })
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: '標記已審' }))
    expect(await screen.findByText('段落內容已變更，請重新載入後再審核')).toBeInTheDocument()
    expect(api.getDraft).toHaveBeenCalledTimes(2)
    await userEvent.click(screen.getByRole('button', { name: '已讀原文，確認' }))
    await waitFor(() => expect(screen.queryByText('段落內容已變更，請重新載入後再審核')).not.toBeInTheDocument())
  })

  it('shows the AI error when a rewrite fails', async () => {
    vi.mocked(api.rewriteParagraph).mockRejectedValue(new api.ApiError(503, 'usage_limit: 已達用量上限'))
    renderEditor()
    await userEvent.type(await screen.findByRole('textbox', { name: '重寫指示' }), '更精簡')
    await userEvent.click(screen.getByRole('button', { name: '要求重寫' }))
    expect(await screen.findByText('usage_limit: 已達用量上限')).toBeInTheDocument()
  })

  it('downloads the export and releases the link only later', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      vi.mocked(api.getDraft).mockResolvedValue({ ...draft, blockers: [] })
      vi.mocked(api.exportDraft).mockResolvedValue(new Blob(['x']))
      const create = vi.fn(() => 'blob:x')
      const revoke = vi.fn()
      Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderEditor()
      await user.click(await screen.findByRole('button', { name: '匯出 Word' }))
      await waitFor(() => expect(click).toHaveBeenCalled())
      expect(revoke).not.toHaveBeenCalled()
      vi.advanceTimersByTime(60_000)
      expect(revoke).toHaveBeenCalledWith('blob:x')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('DraftEditor manual repair', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.getDraft).mockResolvedValue(draft)
    vi.mocked(api.patchParagraph).mockResolvedValue(draft.paragraphs[0])
  })

  it('edits the heading and drops citations whose markers were removed', async () => {
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: '修改' }))
    const heading = screen.getByRole('textbox', { name: '段落標題' })
    await userEvent.clear(heading)
    await userEvent.type(heading, '㈠關於被告過失')
    const box = screen.getByRole('textbox', { name: '段落內文' })
    await userEvent.clear(box)
    await userEvent.type(box, '被告有過失。')
    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(api.patchParagraph).toHaveBeenCalledWith('p1', '被告有過失。', [], '㈠關於被告過失')
  })

  it('removes a paragraph only after a second confirmation', async () => {
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: '刪除本段' }))
    expect(api.patchParagraph).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '確定刪除' }))
    expect(api.patchParagraph).toHaveBeenCalledWith('p1', '', [], '')
  })
})

describe('DraftEditor party-claim confirmation', () => {
  const warnCitation: api.CitationView = { id: 'cj7', code: 'J7', kind: 'judgment', quote: '上訴人雖主張系爭汽車修理費用不應計算折舊',
    label: '臺灣臺中地方法院113年度簡上字第211號民事判決', verify_status: 'warn', verify_reasons: ['possible_party_claim'],
    lawyer_confirmed: false, court_confirmed: false }
  const warnDraft: api.DraftView = { ...draft, blockers: [{ paragraph_seq: 1, reason: 'party_claim_unconfirmed:J7' }],
    paragraphs: [{ ...draft.paragraphs[0], citations: [warnCitation] }] }

  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.getDraft).mockResolvedValue(warnDraft)
    vi.mocked(api.getCitationSource).mockResolvedValue({ kind: 'judgment', title: '臺中地院（法院判斷段）',
      text: '上訴人雖主張系爭汽車修理費用不應計算折舊云云。', span: [0, 20] })
  })

  it('shows a yellow party-claim citation with a court-confirm button in the source pane', async () => {
    vi.mocked(api.confirmCourt).mockResolvedValue({ ...warnCitation, court_confirmed: true })
    renderEditor()
    const tag = await screen.findByRole('button', { name: /J7/ })
    expect(tag).toHaveAttribute('data-state', 'warn')
    expect(screen.queryByRole('button', { name: '已讀原文，確認' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '已讀原文，此句為法院判斷' })).not.toBeInTheDocument()
    await userEvent.click(tag)
    const pane = await screen.findByTestId('source-pane')
    await userEvent.click(await within(pane).findByRole('button', { name: '已讀原文，此句為法院判斷' }))
    expect(api.confirmCourt).toHaveBeenCalledWith('cj7')
  })

  it('hides the court-confirm button from assistants', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...warnDraft, can_review: false })
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: /J7/ }))
    const pane = await screen.findByTestId('source-pane')
    await within(pane).findByText('臺中地院（法院判斷段）')
    expect(within(pane).queryByRole('button', { name: '已讀原文，此句為法院判斷' })).not.toBeInTheDocument()
  })
})

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import DraftEditor from './DraftEditor'
import { reasonLabel } from '../components/ExportButton'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getDraft: vi.fn(), getCitationSource: vi.fn(), confirmCitation: vi.fn(), confirmCourt: vi.fn(), reviewParagraph: vi.fn(),
    patchParagraph: vi.fn(), rewriteParagraph: vi.fn(), exportDraft: vi.fn(), confirmAmount: vi.fn(),
    ackAmounts: vi.fn(), patchRound: vi.fn(), getEvidenceNotes: vi.fn(), getTimeline: vi.fn(), getPageSource: vi.fn(),
    listPins: vi.fn(), insertPin: vi.fn(), verifyPage: vi.fn() }
})

const draft: api.DraftView = {
  id: 'dr1', round_id: 'r1', case_id: 'c1', case_title: '林大明車禍案', can_review: true, brief_title: '民事準備書狀',
  procedure_warning: null,
  brief_kind: 'preparatory', case_type: 'traffic_tort', statement: null, statement_label: null, body_label: null,
  blockers: [{ paragraph_seq: 1, reason: 'unreviewed' }, { paragraph_seq: 1, reason: 'judgment_unconfirmed:J1' }],
  paragraphs: [{
    id: 'p1', seq: 1, heading: '㈠關於路權', display_heading: '㈡關於路權', heading_text: '關於路權',
    text: '按{{cite:J1}}認被告{{quote:J1}}。',
    rendered_text: '按臺灣臺中地方法院113年度中簡字第3946號民事判決認被告「未在適當位置暫停禮讓幹線道之系爭車輛」。',
    version: 'v1', status: 'unreviewed', verify_reasons: [], reviewed_bar_number: null, reviewed_at: null,
    citations: [{ id: 'cj1', code: 'J1', kind: 'judgment', quote: '未在適當位置暫停禮讓幹線道之系爭車輛',
      label: '臺灣臺中地方法院113年度中簡字第3946號民事判決', verify_status: 'ok', verify_reasons: [],
      lawyer_confirmed: false, court_confirmed: false }],
  }],
}

function renderEditor(pollMs?: number) {
  return render(
    <MemoryRouter initialEntries={['/drafts/dr1']}>
      <Routes><Route path="/drafts/:draftId" element={<DraftEditor pollMs={pollMs} />} /></Routes>
    </MemoryRouter>,
  )
}

describe('DraftEditor', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.getDraft).mockResolvedValue(draft)
  })

  it('draft shows unverified OCR banner and source notice', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, unverified_ocr_pages: ['原證2 第1頁', '對造書狀「答辯狀.pdf」第2頁'],
      paragraphs: [{ ...draft.paragraphs[0], source_notice: '依據頁已修正' }] })
    renderEditor()
    expect(await screen.findByText(
      '本輪資料來源含未對照原圖的辨識文字（原證2 第1頁、對造書狀「答辯狀.pdf」第2頁）；日期、姓名、車號等內容請逐段核對原圖。',
    )).toBeInTheDocument()
    expect(screen.getByText('依據頁已修正').className).toMatch(/amber/)
  })

  it('no banner when every OCR page is checked', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, unverified_ocr_pages: [] })
    renderEditor()
    await screen.findByText(/未在適當位置暫停禮讓幹線道之系爭車輛」。/)
    expect(screen.queryByText(/本輪資料來源含未對照原圖的辨識文字/)).not.toBeInTheDocument()
  })

  it('evidence citation shows page image and lawyer can verify', async () => {
    const ev: api.CitationView = { id: 'ce2', code: 'E2', kind: 'evidence', quote: '診斷證明書：右腕骨折，宜休養四週',
      label: '原證2', verify_status: 'ok', verify_reasons: [], lawyer_confirmed: false, court_confirmed: false }
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, blockers: [{ paragraph_seq: 1, reason: 'image_unverified:E2' }],
      paragraphs: [{ ...draft.paragraphs[0], text: '按{{cite:E2}}。', citations: [ev] }] })
    const source: api.CitationSource = { kind: 'evidence', title: '原證2 第1頁（掃描.pdf）',
      text: '診斷證明書：右腕骨折，宜休養四週，需專人照護兩週', span: [0, 16], page_id: 'p2', has_image: true,
      needs_check: true, version: 1 }
    vi.mocked(api.getCitationSource).mockResolvedValueOnce(source).mockResolvedValue({ ...source, needs_check: false })
    vi.mocked(api.verifyPage).mockResolvedValue({ page_id: 'p2', document_id: 'd2', label: '原證2 第1頁',
      ocr_state: 'done', ocr_error: null, text: source.text, version: 1, source: 'ai_ocr', needs_check: false,
      verified: { by_name: '王律師', at: null }, alerts: [], has_image: true, can_edit: true, can_verify: false,
      blank_unconfirmed: false })
    renderEditor()
    expect(await screen.findByText('第 1 段：E2 的頁尚未對照原圖')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /E2/ }))
    const pane = await screen.findByTestId('source-pane')
    expect(within(pane).getByRole('img', { name: '原證2 第1頁（掃描.pdf） 原圖' })).toHaveAttribute('src', '/api/pages/p2/image')
    expect(within(pane).getByText('辨識文字，未對照原圖')).toBeInTheDocument()
    const before = vi.mocked(api.getDraft).mock.calls.length
    await userEvent.click(within(pane).getByRole('button', { name: '已對照原圖' }))
    expect(api.verifyPage).toHaveBeenCalledWith('p2', 1)
    await waitFor(() => expect(vi.mocked(api.getDraft).mock.calls.length).toBe(before + 1))
    await waitFor(() => expect(within(pane).queryByRole('button', { name: '已對照原圖' })).not.toBeInTheDocument())
  })

  it('assistants see the image but no verify button', async () => {
    const ev: api.CitationView = { id: 'ce2', code: 'E2', kind: 'evidence', quote: '診斷證明書：右腕骨折，宜休養四週',
      label: '原證2', verify_status: 'ok', verify_reasons: [], lawyer_confirmed: false, court_confirmed: false }
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, can_review: false,
      paragraphs: [{ ...draft.paragraphs[0], text: '按{{cite:E2}}。', citations: [ev] }] })
    vi.mocked(api.getCitationSource).mockResolvedValue({ kind: 'evidence', title: '原證2 第1頁（掃描.pdf）',
      text: '診斷證明書', span: null, page_id: 'p2', has_image: true, needs_check: true, version: 1 })
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: /E2/ }))
    const pane = await screen.findByTestId('source-pane')
    expect(within(pane).getByRole('img')).toBeInTheDocument()
    expect(within(pane).queryByRole('button', { name: '已對照原圖' })).not.toBeInTheDocument()
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
      [{ code: 'J1', quote: '未在適當位置暫停禮讓幹線道之系爭車輛' }], '關於路權')  // 表單是去號後的標題
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

describe('DraftEditor numbering', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.getDraft).mockResolvedValue(draft)
    vi.mocked(api.patchParagraph).mockResolvedValue(draft.paragraphs[0])
  })

  it('shows the system numbering and edits the heading without it', async () => {
    renderEditor()
    expect(await screen.findByRole('heading', { name: '㈡關於路權' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '修改' }))
    expect(screen.getByRole('textbox', { name: '段落標題' })).toHaveValue('關於路權')
  })

  it('explains a numbered cross reference', () => {
    const label = reasonLabel('numbered_cross_reference:如前(二)所述')
    expect(label).toContain('如前(二)所述')
    expect(label).not.toContain('其他問題')
  })
})

describe('DraftEditor procedure', () => {
  const WARNING = '案號字別「家訴」屬家事程序，與「侵害配偶權」預設的民事程序不同'
  beforeEach(() => vi.resetAllMocks())

  it('shows the procedure warning next to export', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, blockers: [], brief_title: '家事準備書狀',
      procedure_warning: WARNING })
    vi.mocked(api.exportDraft).mockResolvedValue(new Blob(['x']))
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderEditor()
    expect(await screen.findByRole('heading', { name: '家事準備書狀（草稿）' })).toBeInTheDocument()
    const warning = screen.getByText(WARNING)
    expect(warning).toHaveClass('bg-yellow-50')
    const area = screen.getByTestId('export-area')
    expect(within(area).getByText(WARNING)).toBe(warning)
    const button = within(area).getByRole('button', { name: '匯出 Word' })
    expect(button).toBeEnabled()  // 提醒但不阻擋
    await userEvent.click(button)
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('家事準備書狀.docx')
  })

  it('has no warning for a matching procedure', async () => {
    vi.mocked(api.getDraft).mockResolvedValue(draft)
    renderEditor()
    expect(await screen.findByRole('heading', { name: '民事準備書狀（草稿）' })).toBeInTheDocument()
    expect(screen.queryByText(/案號字別/)).not.toBeInTheDocument()
  })

  // ---- 子專案甲 Task 8：聲明區塊與金額核對 ----
  const statement: api.ParagraphView = {
    id: 'st1', seq: 0, kind: 'statement', heading: '', display_heading: '', heading_text: '',
    text: '一、被告應給付原告新臺幣{{amount}}元。\n二、訴訟費用由被告負擔。',
    rendered_text: '一、被告應給付原告新臺幣405,000元。\n二、訴訟費用由被告負擔。',
    version: 'sv1', status: 'unreviewed', verify_reasons: [], reviewed_bar_number: null, reviewed_at: null, citations: [] }
  const complaint: api.DraftView = { ...draft, brief_kind: 'complaint', brief_title: '民事起訴狀', statement,
    statement_label: '訴之聲明', body_label: '事實及理由',
    blockers: [{ paragraph_seq: 0, paragraph_no: 0, reason: 'unreviewed', scope: 'statement' },
      { paragraph_seq: 0, paragraph_no: 0, reason: 'claim_value_missing', scope: 'draft' }] }

  it('statement block renders above body and is reviewable', async () => {
    vi.mocked(api.getDraft).mockResolvedValue(complaint)
    vi.mocked(api.reviewParagraph).mockResolvedValue({ ...statement, status: 'reviewed' })
    renderEditor()
    const block = await screen.findByTestId('statement-block')
    expect(within(block).getByText('訴之聲明')).toBeInTheDocument()
    expect(within(block).getByText(/被告應給付原告新臺幣405,000元/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '事實及理由' })).toBeInTheDocument()
    const order = Array.from(document.querySelectorAll('[data-testid="statement-block"], article'))
    expect(order[0]).toBe(block)
    await userEvent.click(within(block).getByRole('button', { name: '標記已審' }))
    expect(api.reviewParagraph).toHaveBeenCalledWith('st1', 'sv1')
    expect(screen.getByText('整份書狀：請填寫訴訟標的金額或價額')).toBeInTheDocument()
    expect(screen.getByText('聲明：段落尚未審核')).toBeInTheDocument()
  })

  it('complaint lets the lawyer fill in a missing claim value', async () => {
    // 只有非金錢請求（分割遺產）時，訴訟標的價額要律師填，否則無法匯出
    vi.mocked(api.getDraft).mockResolvedValue({ ...complaint, case_type: 'estate_partition',
      claim_value: { label: '訴訟標的價額', value: null } })
    vi.mocked(api.patchRound).mockResolvedValue({})
    renderEditor()
    const input = await screen.findByLabelText('訴訟標的價額（新臺幣元）')
    expect(input).toHaveValue(null)
    await userEvent.type(input, '2255150')
    await userEvent.click(screen.getByRole('button', { name: '儲存訴訟標的價額' }))
    expect(api.patchRound).toHaveBeenCalledWith('r1', { claim_value: 2255150 })
    await waitFor(() => expect(api.getDraft).toHaveBeenCalledTimes(2))
  })

  it('money complaint shows the computed claim value', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...complaint, claim_value: { label: '訴訟標的金額', value: 405000 } })
    renderEditor()
    expect(await screen.findByLabelText('訴訟標的金額（新臺幣元）')).toHaveValue(405000)
  })

  it('no claim value field for other brief kinds', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...complaint, brief_kind: 'answer', claim_value: null })
    renderEditor()
    await screen.findByTestId('statement-block')
    expect(screen.queryByLabelText(/訴訟標的/)).not.toBeInTheDocument()
  })

  it('estate answer shows the alternative-partition hint', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...complaint, brief_kind: 'answer', case_type: 'estate_partition',
      brief_title: '家事答辯狀', statement_label: '答辯聲明', body_label: '答辯理由' })
    renderEditor()
    expect(await screen.findByText('如主張其他分割方法，請改寫聲明並附附表')).toBeInTheDocument()
  })

  it('estate complaint reminds the schedule is filled in Word', async () => {
    vi.mocked(api.getDraft).mockResolvedValue({ ...complaint, case_type: 'estate_partition' })
    renderEditor()
    expect(await screen.findByText('附表需在 Word 填寫')).toBeInTheDocument()
  })

  it('confirm amount button visible to lawyers only and calls confirmAmount', async () => {
    const flagged = { ...draft.paragraphs[0], verify_reasons: ['amount_unverified:406000'] }
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, paragraphs: [flagged] })
    vi.mocked(api.confirmAmount).mockResolvedValue(flagged)
    const { unmount } = renderEditor()
    expect(await screen.findByText('金額未對到請求項目或卷證：406,000元')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '已核對金額 406,000元' }))
    expect(api.confirmAmount).toHaveBeenCalledWith('p1', 406000)
    unmount()
    vi.mocked(api.getDraft).mockResolvedValue({ ...draft, can_review: false, paragraphs: [flagged] })
    renderEditor()
    expect(await screen.findByText('金額未對到請求項目或卷證：406,000元')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '已核對金額 406,000元' })).not.toBeInTheDocument()
  })

  it('lawyer acknowledges self-written amounts in the statement', async () => {
    const st = { ...statement, verify_reasons: ['amounts_unchecked'] }
    vi.mocked(api.getDraft).mockResolvedValue({ ...complaint, statement: st })
    vi.mocked(api.ackAmounts).mockResolvedValue({})
    renderEditor()
    const block = await screen.findByTestId('statement-block')
    expect(within(block).getByText('多位原告或非連帶被告：請律師核對金額')).toBeInTheDocument()
    await userEvent.click(within(block).getByRole('button', { name: '金額已自行核對' }))
    expect(api.ackAmounts).toHaveBeenCalledWith('r1')
  })
})

describe('DraftEditor side tabs', () => {
  const event: api.TimelineEventView = { id: 'e1', date: '2025-03-09', date_label: '民國114年3月9日',
    description: '原告支付醫療費用', amount: 45000, source_kind: 'evidence', origin: 'ai',
    source: { page_id: 'p1', label: '原證1 第1頁', quote: '支付醫療費用', status: 'found', reasons: [] } }
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.getDraft).mockResolvedValue(draft)
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [], job: null })
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [event], pending_ocr: [], job: null })
    vi.mocked(api.getPageSource).mockResolvedValue({ kind: 'evidence', title: '原證1 第1頁',
      text: '收據：支付醫療費用新臺幣45,000元', span: [3, 9] })
  })

  it('left side tabs show notes and timeline and open page source', async () => {
    renderEditor()
    expect(await screen.findByRole('tab', { name: '原文對照' })).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(screen.getByRole('tab', { name: '大事記' }))
    expect(screen.queryByRole('button', { name: '整理大事記' })).not.toBeInTheDocument()  // 草稿頁只看不改
    await userEvent.click(await screen.findByRole('button', { name: '原文出處：原證1 第1頁' }))
    expect(api.getPageSource).toHaveBeenCalledWith('p1', '支付醫療費用')
    expect(screen.getByRole('tab', { name: '原文對照' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('source-pane').querySelector('mark')?.textContent).toBe('支付醫療費用')
    await userEvent.click(screen.getByRole('tab', { name: '卷證說明' }))
    expect(api.getEvidenceNotes).toHaveBeenCalledWith('c1')
    expect(screen.queryByRole('button', { name: '產生卷證說明' })).not.toBeInTheDocument()
  })

  it('draft page has the 已釘選判決 tab', async () => {
    vi.mocked(api.listPins).mockResolvedValue([])
    renderEditor()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['原文對照', '卷證說明', '大事記', '已釘選判決'])
    await userEvent.click(screen.getByRole('tab', { name: '已釘選判決' }))
    expect(api.listPins).toHaveBeenCalledWith('c1')
    expect(await screen.findByText('還沒有釘選的判決：請到案件頁的「判決檢索」分頁搜尋並釘選。')).toBeInTheDocument()
  })

  it('refreshes while a support check is pending and stops once it is done', async () => {
    const withSupport = (support: api.SupportView): api.DraftView => ({ ...draft, paragraphs: [{ ...draft.paragraphs[0],
      citations: [{ ...draft.paragraphs[0].citations[0], support }] }] })
    const pending = withSupport({ status: 'pending', verdict: null, explanation: null, error: null, blocking: true,
      released: null })
    const checked = withSupport({ status: 'done', verdict: 'not_supported', explanation: '原文沒有提到闖紅燈',
      error: null, blocking: true, released: null })
    vi.mocked(api.getDraft).mockResolvedValueOnce(pending).mockResolvedValueOnce(pending).mockResolvedValue(checked)
    renderEditor(20)
    expect(await screen.findAllByText('支持度檢查中')).toHaveLength(2)  // 引用旁與匯出區
    expect(await screen.findByText('不支持')).toBeInTheDocument()
    expect(screen.getByText('原文沒有提到闖紅燈')).toBeInTheDocument()
    const calls = vi.mocked(api.getDraft).mock.calls.length
    await new Promise((r) => setTimeout(r, 100))
    expect(vi.mocked(api.getDraft).mock.calls.length).toBe(calls)
  })
})

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { OPTIONS } from '../test/caseOptions'
import CaseDetail from './CaseDetail'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getCase: vi.fn(), getRound: vi.fn(), createRound: vi.fn(), extractRound: vi.fn(), draftRound: vi.fn(),
    resetRound: vi.fn(), patchClaim: vi.fn(), copyRound: vi.fn(), patchRound: vi.fn(), confirmClaim: vi.fn(),
    addRequest: vi.fn(), deleteClaim: vi.fn(), uploadDocument: vi.fn(),
    renumberExhibit: vi.fn(), getCaseOptions: vi.fn(), patchCase: vi.fn(), putParties: vi.fn(), setDocumentFiler: vi.fn(),
    getEvidenceNotes: vi.fn(), getTimeline: vi.fn() }
})

const emptyHeader: api.CaseHeader = { court: null, case_number: null, division: null, cause: null, addressee: null,
  attorneys: [], reference_date: null }

const baseCase: api.CaseDetail = {
  id: 'c1', title: '林大明車禍案', our_side: 'plaintiff', case_type: 'traffic_tort',
  header: emptyHeader, parties: [], procedure: 'civil', brief_title: '民事準備書狀', addressee_default: '民事庭',
  procedure_warning: null, reference_date_label: '事故發生日',
  documents: [
    { id: 'd1', kind: 'evidence', filename: '01_研判表.txt', round_id: null, filed_by_party_id: null, page_count: 1,
      pages: [{ id: 'p1', page_no: 1, needs_ocr: false }] },
    { id: 'd2', kind: 'evidence', filename: '02_掃描收據.pdf', round_id: null, filed_by_party_id: null, page_count: 1,
      pages: [{ id: 'p2', page_no: 1, needs_ocr: true }] },
  ],
  exhibits: [
    { id: 'e1', label: '原證1', prefix: '原證', number: 1, document_id: 'd1', filename: '01_研判表.txt' },
    { id: 'e2', label: '原證2', prefix: '原證', number: 2, document_id: 'd2', filename: '02_掃描收據.pdf' },
  ],
  rounds: [{ id: 'r1', seq: 1, status: 'failed', error: "usage_limit: You've hit your session limit",
    brief_kind: 'preparatory', failed_stage: 'extract', copied_from_seq: null }],
  brief_kind_options: [{ code: 'complaint', label: '起訴狀' }, { code: 'preparatory', label: '準備書狀' }],
  default_brief_kind: 'preparatory',
}

function renderAt(state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/cases/c1', state }]}>
      <Routes><Route path="/cases/:caseId" element={<CaseDetail />} /></Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getCaseOptions).mockResolvedValue(OPTIONS)
  vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [], job: null })
  vi.mocked(api.getTimeline).mockResolvedValue({ events: [], pending_ocr: [], job: null })
})

describe('CaseDetail', () => {
  it('shows the evidence notes and timeline sections after the exhibits', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    renderAt()
    expect(await screen.findByRole('button', { name: '產生卷證說明' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '整理大事記' })).toBeInTheDocument()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings.indexOf('卷證說明')).toBe(headings.indexOf('卷證') + 1)
    expect(headings.indexOf('大事記')).toBe(headings.indexOf('卷證') + 2)
    expect(api.getEvidenceNotes).toHaveBeenCalledWith('c1')
    expect(api.getTimeline).toHaveBeenCalledWith('c1')
  })


  it('lists exhibits with numbers and marks scanned pages as 待辨識', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'failed', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: null,
      error: "usage_limit: You've hit your session limit", claims: [] })
    renderAt()
    expect(await screen.findByText('原證1')).toBeInTheDocument()
    expect(screen.getByText('原證2')).toBeInTheDocument()
    expect(screen.getByText('待辨識')).toBeInTheDocument()
  })

  it('renumbers an exhibit and reloads the case', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.renumberExhibit).mockResolvedValue({ id: 'e1', label: '原證7', number: 7 })
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '改號 原證1' }))
    const box = screen.getByRole('spinbutton', { name: '原證1 新號碼' })
    await userEvent.clear(box)
    await userEvent.type(box, '7')
    const before = vi.mocked(api.getCase).mock.calls.length
    await userEvent.click(screen.getByRole('button', { name: '儲存號碼' }))
    expect(api.renumberExhibit).toHaveBeenCalledWith('e1', 7)
    expect(vi.mocked(api.getCase).mock.calls.length).toBe(before + 1)
  })

  it('shows the error of a failed round', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    renderAt()
    expect(await screen.findByText(/usage_limit: You've hit your session limit/)).toBeInTheDocument()
  })

  it('links to the draft once the round is drafted', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: 'dr1',
      error: null, claims: [] })
    renderAt()
    const link = await screen.findByRole('link', { name: '開啟草稿' })
    expect(link).toHaveAttribute('href', '/drafts/dr1')
  })
})

describe('CaseDetail references', () => {
  it('shows references the AI wanted but the system could not use', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: 'dr1',
      error: null, claims: [{ id: 'k1', seq: 1, kind: 'opponent_claim', selected: true, summary: '原告與有過失', quote: 'q', quote_verified: true, page_no: 1, references: [
        { text: '民法第184條第1項前段', status: 'mapped', code: 'S1' },
        { text: '臺灣臺中地方法院113年度中簡字第3946號民事判決', status: 'ambiguous_passage', code: null },
        { text: '民法第1030條之1', status: 'unmatched', code: null },
      ] }] })
    renderAt()
    expect(await screen.findByText('系統對回：民法第184條第1項前段')).toBeInTheDocument()
    expect(screen.getByText('對到判決但無法確定段落：臺灣臺中地方法院113年度中簡字第3946號民事判決')).toBeInTheDocument()
    expect(screen.getByText('不在候選清單：民法第1030條之1')).toBeInTheDocument()
  })
})

const PARTIES: api.Party[] = [
  { id: 'pt1', seq: 1, name: '宋文彬', role: '原告', alignment: 'ours', legal_rep: null, position_note: null },
  { id: 'pt2', seq: 2, name: '宋文德', role: '被告', alignment: 'opposing', legal_rep: null, position_note: null },
  { id: 'pt3', seq: 3, name: '宋文琪', role: '被告', alignment: 'aligned', legal_rep: null,
    position_note: '同意分割，但爭執遺產範圍' },
]
const WARNING = '案號字別「家訴」屬家事程序，與「侵害配偶權」預設的民事程序不同，書狀名稱與受文已依案號改為家事'

describe('CaseDetail header and parties', () => {
  it('shows a procedure warning', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, case_type: 'spousal_rights', procedure: 'family',
      brief_title: '家事準備書狀', addressee_default: '臺灣臺北地方法院家事法庭', procedure_warning: WARNING,
      header: { ...emptyHeader, court: '臺灣臺北地方法院', case_number: '113年度家訴字第5號' }, rounds: [] })
    renderAt()
    expect(await screen.findByText(WARNING)).toHaveClass('bg-yellow-50')
    expect(screen.getByText('家事起訴狀／家事準備書狀（依各輪種類）')).toBeInTheDocument()
    expect(screen.getByText('113年度家訴字第5號')).toBeInTheDocument()
    expect(screen.getByText(/預設：臺灣臺北地方法院家事法庭/)).toBeInTheDocument()  // 受文沒填時顯示系統預設
  })

  it('has no warning when the procedure matches', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    renderAt()
    // 案件層不寫死「準備書狀」：第一輪可能是起訴狀，名稱依各輪種類
    expect(await screen.findByText('民事起訴狀／民事準備書狀（依各輪種類）')).toBeInTheDocument()
    expect(screen.queryByText(/案號字別/)).not.toBeInTheDocument()
  })

  it('lists the parties with their alignment', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: PARTIES, rounds: [] })
    renderAt()
    const rows = await screen.findAllByRole('listitem', { name: /當事人/ })
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('原告 宋文彬（我方）'),
      expect.stringContaining('被告 宋文德（對造）'),
      expect.stringContaining('被告 宋文琪（立場與我方相同）'),
    ])
    expect(rows[2]).toHaveTextContent('同意分割，但爭執遺產範圍')
  })

  it('edits the header and the parties', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: PARTIES.slice(0, 1), rounds: [],
      header: { ...emptyHeader, attorneys: ['王大明律師'] } })
    vi.mocked(api.patchCase).mockResolvedValue({ id: 'c1', title: '林大明車禍案', our_side: 'plaintiff',
      case_type: 'traffic_tort' })
    vi.mocked(api.putParties).mockResolvedValue([])
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '修改狀頭' }))
    expect(screen.getByLabelText('訴訟代理人（以逗號分隔）')).toHaveValue('王大明律師')
    await userEvent.type(screen.getByLabelText('案號'), '113年度訴字第1號')
    await userEvent.type(screen.getByLabelText('受文'), '臺灣臺中地方法院民事庭')
    const before = vi.mocked(api.getCase).mock.calls.length
    await userEvent.click(screen.getByRole('button', { name: '儲存狀頭' }))
    expect(api.patchCase).toHaveBeenCalledWith('c1', expect.objectContaining({ case_type: 'traffic_tort',
      case_number: '113年度訴字第1號', addressee: '臺灣臺中地方法院民事庭', court: null, attorneys: ['王大明律師'] }))
    expect(vi.mocked(api.getCase).mock.calls.length).toBe(before + 1)

    await userEvent.click(screen.getByRole('button', { name: '修改當事人' }))
    await userEvent.click(screen.getByRole('button', { name: '新增當事人' }))
    await userEvent.type(screen.getByLabelText('當事人2 姓名'), '林大華')
    await userEvent.selectOptions(screen.getByLabelText('當事人2 角色'), '被告')
    await userEvent.click(screen.getByRole('button', { name: '儲存當事人' }))
    expect(api.putParties).toHaveBeenCalledWith('c1', [
      { id: 'pt1', name: '宋文彬', role: '原告', alignment: 'ours', legal_rep: null, position_note: null },
      { name: '林大華', role: '被告', alignment: 'opposing', legal_rep: null, position_note: null },
    ])
  })

  it('uploads an opponent brief with its filer', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: PARTIES,
      rounds: [{ id: 'r1', seq: 1, status: 'created', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'created', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: null,
      error: null, claims: [] })
    vi.mocked(api.uploadDocument).mockResolvedValue({ id: 'd9', page_count: 1 })
    renderAt()
    const filer = await screen.findByLabelText('遞狀人')
    expect(filer).toHaveValue('pt2')  // 只有一位對造：預設就是他
    expect(within(filer).queryByRole('option', { name: /宋文彬/ })).not.toBeInTheDocument()  // 我方不會是對造書狀的遞狀人
    await userEvent.selectOptions(filer, 'pt3')
    const file = new File(['被告主張原告請求無理由等語。'], '答辯狀.txt', { type: 'text/plain' })
    await userEvent.upload(screen.getByLabelText('上傳對造書狀'), file)
    expect(api.uploadDocument).toHaveBeenCalledWith('c1', file, 'opponent_brief', 'r1', 'pt3')
  })
})

describe('CaseDetail brief filer and notices', () => {
  const TWO_OPPOSING: api.Party[] = [...PARTIES,
    { id: 'pt4', seq: 4, name: '宋文華', role: '被告', alignment: 'opposing', legal_rep: null, position_note: null }]
  const created = [{ id: 'r1', seq: 1, status: 'created' as const, error: null }]

  beforeEach(() => {
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'created', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: null,
      error: null, claims: [] })
  })

  it('leaves the filer unspecified when there are several opposing parties', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: TWO_OPPOSING, rounds: created })
    renderAt()
    expect(await screen.findByLabelText('遞狀人')).toHaveValue('')
    expect(screen.getByText(/對造有好幾位/)).toBeInTheDocument()
  })

  it('shows and changes the filer of an uploaded brief', async () => {
    const brief: api.CaseDocument = { id: 'd3', kind: 'opponent_brief', filename: '答辯狀.txt', round_id: 'r1',
      filed_by_party_id: 'pt2', page_count: 2, pages: [] }
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: TWO_OPPOSING, rounds: created,
      documents: [...baseCase.documents, brief] })
    vi.mocked(api.setDocumentFiler).mockResolvedValue({ id: 'd3', filed_by_party_id: 'pt4' })
    renderAt()
    const select = await screen.findByLabelText('答辯狀.txt 的遞狀人')
    expect(select).toHaveValue('pt2')
    const before = vi.mocked(api.getCase).mock.calls.length
    await userEvent.selectOptions(select, 'pt4')
    expect(api.setDocumentFiler).toHaveBeenCalledWith('d3', 'pt4')
    await waitFor(() => expect(vi.mocked(api.getCase).mock.calls.length).toBe(before + 1))
  })

  it('shows the filer of a drafted round without letting it change', async () => {
    const brief: api.CaseDocument = { id: 'd3', kind: 'opponent_brief', filename: '答辯狀.txt', round_id: 'r1',
      filed_by_party_id: null, page_count: 2, pages: [] }
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: TWO_OPPOSING,
      rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null }], documents: [brief] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: 'dr1',
      error: null, claims: [] })
    renderAt()
    expect(await screen.findByText('答辯狀.txt：遞狀人未指定')).toBeInTheDocument()
    expect(screen.queryByLabelText('答辯狀.txt 的遞狀人')).not.toBeInTheDocument()
  })

  it('clears the notice and old errors after the parties are saved', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: PARTIES.slice(0, 1), rounds: [] })
    vi.mocked(api.patchCase).mockRejectedValueOnce(new api.ApiError(422, '案號太長'))
    vi.mocked(api.putParties).mockResolvedValue([])
    renderAt({ notice: '案件已建立，但當事人沒有存成功：網路中斷。請在案件頁重新填寫。' })
    expect(await screen.findByText(/當事人沒有存成功/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '修改狀頭' }))
    await userEvent.click(screen.getByRole('button', { name: '儲存狀頭' }))
    expect(await screen.findByText('案號太長')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '修改當事人' }))
    await userEvent.click(screen.getByRole('button', { name: '儲存當事人' }))
    await waitFor(() => expect(screen.queryByText(/當事人沒有存成功/)).not.toBeInTheDocument())
    expect(screen.queryByText('案號太長')).not.toBeInTheDocument()
  })

  // ---- 子專案甲 Task 2：兩段式執行 ----
  const extracted: api.RoundDetail = { id: 'r1', seq: 1, status: 'extracted', stale: false, draft_id: null, error: null,
    brief_kind: 'preparatory', failed_stage: null,
    claims: [
      { id: 'k1', seq: 1, kind: 'opponent_claim', selected: true, summary: '原告與有過失', quote: 'q1', quote_verified: true, page_no: 1 },
      { id: 'k2', seq: 2, kind: 'opponent_claim', selected: true, summary: '慰撫金過高', quote: 'q2', quote_verified: true, page_no: 2 },
    ] }

  it('shows checkboxes after extract and unticking calls patchClaim', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValueOnce(extracted)
      .mockResolvedValue({ ...extracted, claims: [extracted.claims[0], { ...extracted.claims[1], selected: false }] })
    vi.mocked(api.patchClaim).mockResolvedValue({ ...extracted.claims[1], selected: false })
    renderAt()
    const box = await screen.findByRole('checkbox', { name: '回應 主張2' })
    expect(box).toBeChecked()
    await userEvent.click(box)
    expect(api.patchClaim).toHaveBeenCalledWith('k2', { selected: false })
    expect(await screen.findByText('未勾選的 1 條主張不會被回應')).toBeInTheDocument()
  })

  it('drafts after extract', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue(extracted)
    vi.mocked(api.draftRound).mockResolvedValue({ status: 'drafting' })
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '起草' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1')
  })

  it('reset asks for confirmation on confirm_reset', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue(extracted)
    vi.mocked(api.resetRound).mockRejectedValueOnce(new api.ApiError(409, '會清除勾選，確定要重新拆解嗎？', 'confirm_reset'))
      .mockResolvedValue({ status: 'created' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '重新拆解' }))
    await waitFor(() => expect(api.resetRound).toHaveBeenLastCalledWith('r1', true))
    expect(confirm).toHaveBeenCalledWith('會清除勾選，確定要重新拆解嗎？')
    confirm.mockRestore()
  })

  it('shows restart button when a stage is stale', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'drafting', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, status: 'drafting', stale: true })
    vi.mocked(api.draftRound).mockResolvedValue({ status: 'drafting' })
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '重新開始' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1')
  })

  it('retries the failed stage', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, status: 'failed', failed_stage: 'draft', error: 'timeout: slow' })
    vi.mocked(api.draftRound).mockResolvedValue({ status: 'drafting' })
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '重新起草' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1')
  })

  // ---- 子專案甲 Task 8：書狀種類、起訴狀請求項目、重新起草 ----
  const complaintRound: api.RoundDetail = { ...extracted, brief_kind: 'complaint', can_review: true,
    narrative: '請求醫療費與慰撫金', joint_liability: false, provisional_execution: true, claim_value: null,
    statement_variant: null, total_amount: 45000, pending_ocr_pages: ['原證3 第1頁'],
    statement_preview: '一、被告應給付原告新臺幣45,000元，及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。',
    claims: [
      { id: 'q1', seq: 1, kind: 'request', selected: true, summary: '醫療費用', quote: '醫療費用合計新臺幣45,000元',
        quote_verified: true, page_no: 1, request_type: 'monetary', amount: 45000, amount_note: null, basis_text: null,
        confirmed: true, evidence: [{ exhibit: '原證1', page_no: 1, quote: '醫療費用合計新臺幣45,000元', found: true }] },
      { id: 'q2', seq: 2, kind: 'request', selected: true, summary: '車輛修復費', quote: '', quote_verified: false,
        page_no: null, request_type: 'monetary', amount: 160000, amount_note: 'amount_not_in_evidence', basis_text: null,
        confirmed: false, evidence: [{ exhibit: '原證2', page_no: 1, quote: '修復費160,000元', found: false }] },
    ] }

  it('draft button disabled while request items are unconfirmed, with the reason', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    vi.mocked(api.getRound).mockResolvedValue(complaintRound)
    vi.mocked(api.confirmClaim).mockResolvedValue(complaintRound.claims[1])
    renderAt()
    expect(await screen.findByText('第1輪・起訴狀')).toBeInTheDocument()
    expect(await screen.findByText('還有 1 個請求項目尚未確認')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '起草' })).toBeDisabled()
    expect(screen.getByText('金額未在卷證中找到')).toBeInTheDocument()
    expect(screen.getByText('原文出處：原證1 第1頁')).toBeInTheDocument()
    expect(screen.getByText('未找到原文')).toBeInTheDocument()
    expect(screen.getByText('合計新臺幣45,000元')).toBeInTheDocument()
    expect(screen.getByText(/待辨識，未處理：原證3 第1頁/)).toBeInTheDocument()
    expect(screen.getByText(/被告應給付原告新臺幣45,000元/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '確認 請求項目2' }))
    expect(api.confirmClaim).toHaveBeenCalledWith('q2')
  })

  it('assistants cannot confirm request items', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...complaintRound, can_review: false })
    renderAt()
    expect(await screen.findByText('第1輪・起訴狀')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '確認 請求項目2' })).not.toBeInTheDocument()
  })

  it('complaint asks for a narrative before extracting', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'created', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...complaintRound, status: 'created', narrative: null, claims: [] })
    vi.mocked(api.patchRound).mockResolvedValue({})
    vi.mocked(api.extractRound).mockResolvedValue({ status: 'extracting' })
    renderAt()
    await userEvent.type(await screen.findByLabelText('案情說明'), '原告請求醫療費。')
    await userEvent.click(screen.getByRole('button', { name: '拆解' }))
    expect(api.patchRound).toHaveBeenCalledWith('r1', expect.objectContaining({ narrative: '原告請求醫療費。' }))
    expect(api.extractRound).toHaveBeenCalledWith('r1')
    expect(screen.queryByLabelText('上傳對造書狀')).not.toBeInTheDocument()
  })

  it('saves the complaint narrative when leaving the field', async () => {
    // 子專案甲 M8：案情說明離開欄位就存，重新整理後不會不見
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'created', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...complaintRound, status: 'created', narrative: null, claims: [] })
    vi.mocked(api.patchRound).mockResolvedValue({})
    renderAt()
    await userEvent.type(await screen.findByLabelText('案情說明'), '原告請求醫療費。')
    expect(api.patchRound).not.toHaveBeenCalled()
    await userEvent.tab()
    await waitFor(() => expect(api.patchRound).toHaveBeenCalledTimes(1))
    expect(api.patchRound).toHaveBeenCalledWith('r1', { narrative: '原告請求醫療費。' })
  })

  it('copy button only on drafted rounds', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null, brief_kind: 'preparatory', failed_stage: null,
        copied_from_seq: null }, { id: 'r2', seq: 2, status: 'extracted', error: null, brief_kind: 'preparatory',
        failed_stage: null, copied_from_seq: 1 }] })
    vi.mocked(api.getRound).mockImplementation(async (id: string) => id === 'r1'
      ? { ...extracted, id: 'r1', status: 'drafted', draft_id: 'dr1' } : { ...extracted, id: 'r2', seq: 2 })
    vi.mocked(api.copyRound).mockResolvedValue({ id: 'r3', seq: 3 })
    renderAt()
    expect(await screen.findByText('（由第1輪複製）')).toBeInTheDocument()
    const buttons = await screen.findAllByRole('button', { name: '以這一輪為基礎重新起草' })
    expect(buttons).toHaveLength(1)
    await userEvent.click(buttons[0])
    expect(api.copyRound).toHaveBeenCalledWith('r1')
  })

  it('new round form offers complaint for plaintiff', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [],
      brief_kind_options: [{ code: 'complaint', label: '起訴狀' }, { code: 'preparatory', label: '準備書狀' }],
      default_brief_kind: 'complaint' })
    vi.mocked(api.createRound).mockResolvedValue({ id: 'r1', seq: 1 })
    renderAt()
    const select = await screen.findByLabelText('書狀種類')
    expect(select).toHaveValue('complaint')
    await userEvent.selectOptions(select, 'preparatory')
    await userEvent.click(screen.getByRole('button', { name: '建立新一輪' }))
    expect(api.createRound).toHaveBeenCalledWith('c1', 'preparatory')
  })
})

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
    addRequest: vi.fn(), deleteClaim: vi.fn(), uploadDocument: vi.fn(), uploadDocuments: vi.fn(),
    startCaseOcr: vi.fn(), startDocumentOcr: vi.fn(), reocrDocument: vi.fn(), getPageOcr: vi.fn(),
    renumberExhibit: vi.fn(), getCaseOptions: vi.fn(), patchCase: vi.fn(), putParties: vi.fn(), setDocumentFiler: vi.fn(),
    getEvidenceNotes: vi.fn(), getTimeline: vi.fn(), getHealthReport: vi.fn(), runHealthCheck: vi.fn(),
    getPageSource: vi.fn(), listPins: vi.fn(), searchJudgments: vi.fn() }
})

const emptyHeader: api.CaseHeader = { court: null, case_number: null, division: null, cause: null, addressee: null,
  attorneys: [], reference_date: null }

const baseCase: api.CaseDetail = {
  id: 'c1', title: '林大明車禍案', our_side: 'plaintiff', case_type: 'traffic_tort',
  header: emptyHeader, parties: [], procedure: 'civil', brief_title: '民事準備書狀', addressee_default: '民事庭',
  procedure_warning: null, reference_date_label: '事故發生日',
  documents: [
    { id: 'd1', kind: 'evidence', filename: '01_研判表.txt', round_id: null, filed_by_party_id: null, page_count: 1,
      pages: [{ id: 'p1', page_no: 1, needs_ocr: false, ocr_state: 'none' }] },
    { id: 'd2', kind: 'evidence', filename: '02_掃描收據.pdf', round_id: null, filed_by_party_id: null, page_count: 1,
      pages: [{ id: 'p2', page_no: 1, needs_ocr: true, ocr_state: 'pending', needs_check: false, alert_count: 0,
        verified: false }],
      ocr: { job: null, progress: { total: 1, done: 0, failed: 0, proofing: 0, minutes_left: 1 }, can_reocr: false,
        scan_hint: false, can_resume: true } },
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

function renderAt(state?: unknown, search = '') {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/cases/c1', search, state }]}>
      <Routes><Route path="/cases/:caseId" element={<CaseDetail />} /></Routes>
    </MemoryRouter>,
  )
}

// 案件頁分成分頁（子專案丙）：既有測試先切到對應的分頁，斷言內容不變
async function openTab(name: string) {
  await userEvent.click(await screen.findByRole('tab', { name }))
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getCaseOptions).mockResolvedValue(OPTIONS)
  vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [], job: null })
  vi.mocked(api.getTimeline).mockResolvedValue({ events: [], pending_ocr: [], job: null })
  vi.mocked(api.getHealthReport).mockResolvedValue({ report: null, job: null })
})

describe('CaseDetail', () => {
  it('runs the health check only after drafting', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null },
      { id: 'r2', seq: 2, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockImplementation(async (id: string) => ({ id, seq: id === 'r1' ? 1 : 2,
      status: id === 'r1' ? 'drafted' : 'extracted', error: null, stale: false, brief_kind: 'preparatory',
      failed_stage: null, draft_id: id === 'r1' ? 'dr1' : null, claims: [] }))
    renderAt()
    await openTab('書狀健檢')
    expect(await screen.findAllByRole('button', { name: '執行健檢' })).toHaveLength(1)
    expect(api.getHealthReport).toHaveBeenCalledWith('r1')
    expect(api.getHealthReport).not.toHaveBeenCalledWith('r2')
  })

  it('refetches the health report after new evidence is uploaded', async () => {
    // 子專案乙驗收時在瀏覽器發現：同一頁上傳卷證後，健檢區塊沒有重新讀取，「資料已變更」要重新整理才出現
    const drafted = [{ id: 'r1', seq: 1, status: 'drafted' as const, error: null }]
    const added: api.CaseDocument = { id: 'd9', kind: 'evidence', filename: '06_收據.txt', round_id: null,
      filed_by_party_id: null, page_count: 1, pages: [{ id: 'p9', page_no: 1, needs_ocr: false }] }
    const uploaded = () => vi.mocked(api.uploadDocuments).mock.calls.length > 0
    vi.mocked(api.getCase).mockImplementation(async () => ({ ...baseCase, rounds: drafted,
      documents: uploaded() ? [...baseCase.documents, added] : baseCase.documents }))
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', error: null, stale: false,
      brief_kind: 'preparatory', failed_stage: null, draft_id: 'dr1', claims: [] })
    const report: api.HealthReportView = { created_at: '2026-11-24T00:00:00+00:00', outdated: false, items: [],
      undisputed: [], issues: [], issues_label: '本件爭點', pending_ocr: [] }
    vi.mocked(api.getHealthReport).mockImplementation(async () => ({ report: { ...report, outdated: uploaded() },
      job: null }))
    vi.mocked(api.uploadDocuments).mockResolvedValue({ id: 'd9', page_count: 1 })
    renderAt()
    await openTab('書狀健檢')
    expect(await screen.findByRole('button', { name: '重新執行健檢' })).toBeInTheDocument()
    expect(screen.queryByText('資料已變更，建議重新執行')).not.toBeInTheDocument()
    const file = new File(['計程車收據'], '06_收據.txt', { type: 'text/plain' })
    await openTab('卷證與時間軸')
    await userEvent.upload(screen.getByLabelText('上傳卷證（依上傳順序自動編號）'), file)
    expect(api.uploadDocuments).toHaveBeenCalledWith('c1', [file], 'evidence')
    await openTab('書狀健檢')
    expect(await screen.findByText('資料已變更，建議重新執行')).toBeInTheDocument()
  })

  it('shows the evidence notes and timeline sections after the exhibits', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    renderAt()
    await openTab('卷證與時間軸')
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
    await openTab('卷證與時間軸')
    expect(await screen.findByText('原證1')).toBeInTheDocument()
    expect(screen.getByText('原證2')).toBeInTheDocument()
    expect(screen.getByText('待辨識')).toBeInTheDocument()
  })

  it('shows the start button for old pending pages and the upload hint', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, pending_ocr_pages: 1 })
    vi.mocked(api.startCaseOcr).mockResolvedValue({ jobs: [], pages: 1 })
    renderAt()
    await openTab('卷證與時間軸')
    expect(await screen.findByText('可上傳 PDF、Word、純文字、JPG、PNG；iPhone 的 HEIC 照片請先轉成 JPG')).toBeInTheDocument()
    const before = vi.mocked(api.getCase).mock.calls.length
    await userEvent.click(screen.getByRole('button', { name: '開始辨識（1 頁）' }))
    expect(api.startCaseOcr).toHaveBeenCalledWith('c1', false)
    await waitFor(() => expect(vi.mocked(api.getCase).mock.calls.length).toBe(before + 1))
    expect(screen.getAllByRole('button', { name: /上傳卷證/ }).length).toBe(1)  // 主要按鈕仍只有一個
  })

  it('uploads several photos as one document', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.uploadDocuments).mockResolvedValue({ id: 'd9', page_count: 2, ocr: null })
    renderAt()
    await openTab('卷證與時間軸')
    const a = new File(['a'], 'a.jpg', { type: 'image/jpeg' })
    const b = new File(['b'], 'b.png', { type: 'image/png' })
    const input = await screen.findByLabelText(/上傳卷證（依上傳順序自動編號）/)
    expect(input).toHaveAttribute('accept', '.pdf,.docx,.txt,.jpg,.jpeg,.png')
    await userEvent.upload(input, [a, b])
    expect(api.uploadDocuments).toHaveBeenCalledWith('c1', [a, b], 'evidence')
  })

  it('opens the page check panel from an OCR page tag', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.getPageOcr).mockResolvedValue({ page_id: 'p2', document_id: 'd2', label: '原證2 第1頁',
      ocr_state: 'pending', ocr_error: null, text: null, version: null, source: null, needs_check: false, verified: null,
      alerts: [], has_image: true, can_edit: true, can_verify: false, blank_unconfirmed: false })
    renderAt()
    await openTab('卷證與時間軸')
    await userEvent.click(await screen.findByRole('button', { name: '第1頁：待辨識' }))
    expect(await screen.findByRole('img', { name: '原證2 第1頁 原圖' })).toBeInTheDocument()
    expect(api.getPageOcr).toHaveBeenCalledWith('p2')
  })

  it('renumbers an exhibit and reloads the case', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.renumberExhibit).mockResolvedValue({ id: 'e1', label: '原證7', number: 7 })
    renderAt()
    await openTab('卷證與時間軸')
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
    await openTab('書狀')
    expect(await screen.findByText(/usage_limit: You've hit your session limit/)).toBeInTheDocument()
  })

  it('links to the draft once the round is drafted', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: 'dr1',
      error: null, claims: [] })
    renderAt()
    await openTab('書狀')
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
    await openTab('書狀')
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

  it('keeps unsaved header and party edits across tab switches', async () => {
    // 最終審查 #2：改到一半切去別的分頁再切回來，沒存的狀頭、當事人編輯還在
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: PARTIES.slice(0, 1), rounds: [] })
    renderAt()
    await userEvent.click(await screen.findByRole('button', { name: '修改狀頭' }))
    await userEvent.type(screen.getByLabelText('案號'), '113年度訴字第1號')
    await userEvent.click(screen.getByRole('button', { name: '修改當事人' }))
    await userEvent.type(screen.getByLabelText('當事人1 姓名'), '甲')
    await openTab('卷證與時間軸')
    await openTab('案件資料')
    expect(screen.getByLabelText('案號')).toHaveValue('113年度訴字第1號')
    expect(screen.getByLabelText('當事人1 姓名')).toHaveValue('宋文彬甲')
    expect(api.patchCase).not.toHaveBeenCalled()
    expect(api.putParties).not.toHaveBeenCalled()
  })

  it('uploads an opponent brief with its filer', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, parties: PARTIES,
      rounds: [{ id: 'r1', seq: 1, status: 'created', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'created', stale: false, brief_kind: 'preparatory', failed_stage: null, draft_id: null,
      error: null, claims: [] })
    vi.mocked(api.uploadDocuments).mockResolvedValue({ id: 'd9', page_count: 1 })
    renderAt()
    await openTab('書狀')
    const filer = await screen.findByLabelText('遞狀人')
    expect(filer).toHaveValue('pt2')  // 只有一位對造：預設就是他
    expect(within(filer).queryByRole('option', { name: /宋文彬/ })).not.toBeInTheDocument()  // 我方不會是對造書狀的遞狀人
    await userEvent.selectOptions(filer, 'pt3')
    const file = new File(['被告主張原告請求無理由等語。'], '答辯狀.txt', { type: 'text/plain' })
    await userEvent.upload(screen.getByLabelText('上傳對造書狀'), file)
    expect(api.uploadDocuments).toHaveBeenCalledWith('c1', [file], 'opponent_brief', 'r1', 'pt3')
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
    await openTab('書狀')
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
    await openTab('書狀')
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
    await openTab('書狀')
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
    await openTab('書狀')
    const box = await screen.findByRole('checkbox', { name: '回應 主張2' })
    expect(box).toBeChecked()
    await userEvent.click(box)
    expect(api.patchClaim).toHaveBeenCalledWith('k2', { selected: false })
    expect(await screen.findByText('未勾選的 1 條主張不會被回應')).toBeInTheDocument()
  })

  it('shows each claim source page and quote under its summary', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, claims: [
      { ...extracted.claims[0], quote: '原告於路口未減速慢行致生事故', page_no: 3 },
      { ...extracted.claims[1], quote: '慰撫金請求顯屬過高', page_no: null },
    ] })
    renderAt()
    await openTab('書狀')
    const first = await screen.findByText(/出處：第 3 頁「原告於路口未減速慢行致生事故」/)
    expect(first).toHaveAttribute('title', '原告於路口未減速慢行致生事故')
    expect(screen.getByText('出處：「慰撫金請求顯屬過高」')).toBeInTheDocument()
  })

  it('drafts after extract', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue(extracted)
    vi.mocked(api.draftRound).mockResolvedValue({ status: 'drafting' })
    renderAt()
    await openTab('書狀')
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
    await openTab('書狀')
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
    await openTab('書狀')
    await userEvent.click(await screen.findByRole('button', { name: '重新開始' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1')
  })

  it('retries the failed stage', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, status: 'failed', failed_stage: 'draft', error: 'timeout: slow' })
    vi.mocked(api.draftRound).mockResolvedValue({ status: 'drafting' })
    renderAt()
    await openTab('書狀')
    await userEvent.click(await screen.findByRole('button', { name: '重新起草' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1')
  })

  // ---- 計畫 2A Task 9：對造書狀的辨識、拆解前把關、主張出處 ----
  it('disables extract with the blocking message from the round detail', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'created', error: null,
      brief_kind: 'preparatory' }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, status: 'created', claims: [],
      extract_blocked: '對造書狀第 3 頁尚未辨識，完成後才能拆解' })
    renderAt()
    await openTab('書狀')
    expect(await screen.findByText('對造書狀第 3 頁尚未辨識，完成後才能拆解')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '拆解' })).toBeDisabled()
    expect(screen.getByLabelText('上傳對造書狀')).toHaveAttribute('accept', '.pdf,.docx,.txt,.jpg,.jpeg,.png')
  })

  it('complaint extract asks to confirm when evidence pages are pending', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'created', error: null,
      brief_kind: 'complaint' }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, status: 'created', brief_kind: 'complaint', claims: [],
      narrative: '請求醫療費', pending_ocr_pages: ['原證3 第1頁', '原證4 第2頁'] })
    vi.mocked(api.patchRound).mockResolvedValue({})
    vi.mocked(api.extractRound).mockResolvedValue({ status: 'extracting' })
    const ask = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderAt()
    await openTab('書狀')
    await userEvent.click(await screen.findByRole('button', { name: '拆解' }))
    expect(ask).toHaveBeenCalledWith('卷證原證3 第1頁、原證4 第2頁尚未辨識，請求項目可能不完整，要先拆解嗎？')
    expect(api.extractRound).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '拆解' }))
    await waitFor(() => expect(api.extractRound).toHaveBeenCalledWith('r1'))
    ask.mockRestore()
  })

  it('opponent claims from unverified OCR pages show the tag and open the image', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...extracted, claims: [
      { ...extracted.claims[0], page_id: 'p9', ocr_unverified: true }, extracted.claims[1]] })
    vi.mocked(api.getPageOcr).mockResolvedValue({ page_id: 'p9', document_id: 'd9', label: '對造書狀「答辯狀.pdf」第1頁',
      ocr_state: 'done', ocr_error: null, text: '原告與有過失', version: 1, source: 'ai_ocr', needs_check: true,
      verified: null, alerts: [], has_image: true, can_edit: true, can_verify: false, blank_unconfirmed: false })
    renderAt()
    await openTab('書狀')
    expect(await screen.findByText('辨識文字，未對照原圖')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '看原圖' }))
    expect(await screen.findByRole('img', { name: '對造書狀「答辯狀.pdf」第1頁 原圖' })).toBeInTheDocument()
    expect(api.getPageOcr).toHaveBeenCalledWith('p9')
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
    await openTab('書狀')
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

  it('saves an edited request item when leaving the row, so switching tabs keeps it', async () => {
    // critic R1：改了金額切去卷證分頁核對再回來，不能變回 AI 的金額、讓律師按「確認」確認進訴之聲明
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    let stored = complaintRound
    vi.mocked(api.getRound).mockImplementation(async () => stored)
    vi.mocked(api.patchClaim).mockImplementation(async (id, body) => {
      stored = { ...stored, claims: stored.claims.map((c) => c.id === id
        ? { ...c, summary: body.label ?? c.summary, amount: body.amount ?? null, confirmed: false } : c) }
      return stored.claims.find((c) => c.id === id)!
    })
    renderAt()
    await openTab('書狀')
    const amount = await screen.findByLabelText('請求項目2 金額')
    await userEvent.clear(amount)
    await userEvent.type(amount, '150000')
    await userEvent.click(screen.getByLabelText('請求項目2 名稱'))  // 同一列換欄位：還在改，不存
    expect(api.patchClaim).not.toHaveBeenCalled()
    await openTab('卷證與時間軸')
    await waitFor(() => expect(api.patchClaim).toHaveBeenCalledWith('q2',
      { label: '車輛修復費', request_type: 'monetary', amount: 150000 }))
    await openTab('書狀')
    await waitFor(() => expect(screen.getByLabelText('請求項目2 金額')).toHaveValue('150000'))
  })

  it('assistants cannot confirm request items', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'extracted', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...complaintRound, can_review: false })
    renderAt()
    await openTab('書狀')
    expect(await screen.findByText('第1輪・起訴狀')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '確認 請求項目2' })).not.toBeInTheDocument()
  })

  it('complaint asks for a narrative before extracting', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'created', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ ...complaintRound, status: 'created', narrative: null, claims: [],
      pending_ocr_pages: [] })  // 卷證都辨識完（有待辨識頁時會先問律師，另有測試）
    vi.mocked(api.patchRound).mockResolvedValue({})
    vi.mocked(api.extractRound).mockResolvedValue({ status: 'extracting' })
    renderAt()
    await openTab('書狀')
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
    await openTab('書狀')
    await userEvent.type(await screen.findByLabelText('案情說明'), '原告請求醫療費。')
    expect(api.patchRound).not.toHaveBeenCalled()
    await userEvent.tab()
    await waitFor(() => expect(api.patchRound).toHaveBeenCalledTimes(1))
    expect(api.patchRound).toHaveBeenCalledWith('r1', { narrative: '原告請求醫療費。' })
  })

  it('saves the complaint options when ticked, so switching tabs keeps them', async () => {
    // 最終審查 #2：勾了「被告連帶給付」切去別的分頁再回來，不能悄悄變回沒勾（拆解時聲明會少一項）
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase,
      rounds: [{ id: 'r1', seq: 1, status: 'created', error: null, brief_kind: 'complaint', failed_stage: null,
        copied_from_seq: null }] })
    let stored: api.RoundDetail = { ...complaintRound, status: 'created', narrative: null, claims: [] }
    vi.mocked(api.getRound).mockImplementation(async () => stored)
    vi.mocked(api.patchRound).mockImplementation(async (_id, body) => {
      stored = { ...stored, ...body } as api.RoundDetail
      return {}
    })
    renderAt()
    await openTab('書狀')
    await userEvent.click(await screen.findByLabelText('被告連帶給付'))
    await waitFor(() => expect(api.patchRound).toHaveBeenCalledWith('r1', { joint_liability: true }))
    await userEvent.click(screen.getByLabelText('聲請假執行（只適用金錢請求）'))
    await waitFor(() => expect(api.patchRound).toHaveBeenCalledWith('r1', { provisional_execution: false }))
    await openTab('案件資料')
    await openTab('書狀')
    await waitFor(() => expect(screen.getByLabelText('被告連帶給付')).toBeChecked())
    expect(screen.getByLabelText('聲請假執行（只適用金錢請求）')).not.toBeChecked()
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
    await openTab('書狀')
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
    await openTab('書狀')
    const select = await screen.findByLabelText('書狀種類')
    expect(select).toHaveValue('complaint')
    await userEvent.selectOptions(select, 'preparatory')
    await userEvent.click(screen.getByRole('button', { name: '建立新一輪' }))
    expect(api.createRound).toHaveBeenCalledWith('c1', 'preparatory')
  })
})

describe('CaseDetail tabs', () => {
  const drafted = (id: string, seq: number) => ({ id, seq, status: 'drafted' as const, error: null,
    brief_kind: 'preparatory' as const, failed_stage: null, copied_from_seq: null })
  const roundDetail = (id: string, status: api.RoundStatus) => ({ id, seq: Number(id.slice(1)), status, error: null,
    stale: false, brief_kind: 'preparatory' as const, failed_stage: null, draft_id: status === 'drafted' ? `d${id}` : null,
    claims: [] })

  it('opens on 案件資料 and switches tabs via ?tab=', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    renderAt()
    const info = await screen.findByRole('tab', { name: '案件資料' })
    expect(info).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: '狀頭' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '卷證' })).not.toBeInTheDocument()
    await openTab('書狀')
    expect(screen.getByRole('tab', { name: '書狀' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: '攻防輪次' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '狀頭' })).not.toBeInTheDocument()
  })

  it('opens the tab named in the address', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    renderAt(undefined, '?tab=evidence')
    expect(await screen.findByRole('heading', { name: '卷證' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '卷證與時間軸' })).toHaveAttribute('aria-selected', 'true')
  })

  it('each tab has exactly one amber primary button', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [drafted('r1', 1)] })
    vi.mocked(api.getRound).mockResolvedValue(roundDetail('r1', 'drafted'))
    renderAt()
    vi.mocked(api.listPins).mockResolvedValue([])
    const expected: Record<string, string> = { 案件資料: '修改狀頭', 卷證與時間軸: '上傳卷證', 書狀: '建立新一輪',
      判決檢索: '搜尋', 書狀健檢: '執行健檢' }
    for (const [tab, primary] of Object.entries(expected)) {
      await openTab(tab)
      const panel = screen.getByRole('tabpanel')
      await within(panel).findByRole('button', { name: primary })
      const buttons = within(panel).getAllByRole('button')
      const amber = buttons.filter((b) => b.className.includes('bg-amber-500'))
      expect(amber.map((b) => b.textContent), tab).toEqual([primary])
      expect(buttons[0], tab).toBe(amber[0])
    }
  })

  it('health tab shows the latest drafted round', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [drafted('r1', 1), drafted('r2', 2),
      { ...drafted('r3', 3), status: 'extracted' }] })
    renderAt(undefined, '?tab=health')
    const select = await screen.findByLabelText('健檢的輪次')
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['第1輪・準備書狀', '第2輪・準備書狀'])
    expect(select).toHaveValue('r2')
    await waitFor(() => expect(api.getHealthReport).toHaveBeenCalledWith('r2'))
    expect(api.getHealthReport).not.toHaveBeenCalledWith('r1')
    await userEvent.selectOptions(select, 'r1')
    await waitFor(() => expect(api.getHealthReport).toHaveBeenCalledWith('r1'))
  })

  it('shows the original page from the evidence tab', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    const source: api.SourceView = { page_id: 'p1', label: '原證1 第1頁', quote: '未讓幹道車先行', status: 'found',
      reasons: [] }
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [{ document_id: 'd1', exhibit: '原證1',
      filename: '01_研判表.txt', needs_notes: false, pending_ocr: [],
      notes: [{ id: 'n1', kind: 'proves', text: '可證明被告未讓幹道車先行', origin: 'ai', source }] }], job: null })
    vi.mocked(api.getPageSource).mockResolvedValue({ kind: 'evidence', title: '原證1（01_研判表.txt）第1頁',
      text: '研判結果：未讓幹道車先行。', span: [5, 12], annotation: null })
    renderAt(undefined, '?tab=evidence')
    await userEvent.click(await screen.findByRole('button', { name: '原文出處：原證1 第1頁' }))
    expect(api.getPageSource).toHaveBeenCalledWith('p1', '未讓幹道車先行')
    expect(await screen.findByText('原證1（01_研判表.txt）第1頁')).toBeInTheDocument()
    expect(screen.getByTestId('source-pane')).toBeInTheDocument()
  })

  it('shows minutes while AI runs', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [] })
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [], job: { id: 'j1', status: 'running', error: null,
      started_at: '2026-11-24T00:00:00Z', stale: false } })
    renderAt(undefined, '?tab=evidence')
    expect(await screen.findByText('AI 處理中，約需 1 分鐘')).toBeInTheDocument()
  })
})

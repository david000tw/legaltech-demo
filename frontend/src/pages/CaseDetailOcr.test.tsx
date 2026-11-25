// 辨識進行中的案件頁：只輪詢輕量狀態；辨識結束（狀態變了）才重讀整個案件，卷證說明與大事記跟著重讀。
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { OPTIONS } from '../test/caseOptions'
import CaseDetail from './CaseDetail'

vi.mock('../aiJobs', async (orig) => ({ ...(await orig<typeof import('../aiJobs')>()), POLL_MS: 15 }))
vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getCase: vi.fn(), getCaseOptions: vi.fn(), getDocumentOcrStatus: vi.fn(),
    getEvidenceNotes: vi.fn(), getTimeline: vi.fn() }
})

const job = (status: api.OcrJob['status']): api.OcrJob => ({ id: 'j1', status, error: null,
  started_at: '2026-11-24T10:00:00+00:00', stale: false, document_id: 'd2' })
const RUNNING = { job: job('running'), progress: { total: 1, done: 0, failed: 0, proofing: 0, minutes_left: 1 },
  can_resume: false }
const FINISHED = { job: job('done'), progress: { total: 1, done: 1, failed: 0, proofing: 0, minutes_left: 0 },
  can_resume: false }

function caseWith(ocr: api.DocOcrStatus, pageState: 'pending' | 'done'): api.CaseDetail {
  return {
    id: 'c1', title: '林大明車禍案', our_side: 'plaintiff', case_type: 'traffic_tort',
    header: { court: null, case_number: null, division: null, cause: null, addressee: null, attorneys: [],
      reference_date: null },
    parties: [], procedure: 'civil', brief_title: '民事準備書狀', addressee_default: '民事庭', procedure_warning: null,
    reference_date_label: '事故發生日',
    documents: [{ id: 'd2', kind: 'evidence', filename: '02_掃描收據.pdf', round_id: null, filed_by_party_id: null,
      page_count: 1,
      pages: [{ id: 'p2', page_no: 1, needs_ocr: true, ocr_state: pageState, needs_check: pageState === 'done',
        alert_count: 0, verified: false }],
      ocr: { ...ocr, can_reocr: false, scan_hint: false } }],
    exhibits: [{ id: 'e2', label: '原證2', prefix: '原證', number: 2, document_id: 'd2', filename: '02_掃描收據.pdf' }],
    rounds: [], brief_kind_options: [], default_brief_kind: 'preparatory',
  }
}

const notes = (pending: string[]): { documents: api.EvidenceDocView[]; job: null } => ({
  documents: [{ document_id: 'd2', exhibit: '原證2', filename: '02_掃描收據.pdf', needs_notes: pending.length === 0,
    pending_ocr: pending, notes: [] }],
  job: null })

function renderCase() {
  return render(
    <MemoryRouter initialEntries={['/cases/c1?tab=evidence']}>
      <Routes><Route path="/cases/:caseId" element={<CaseDetail />} /></Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getCaseOptions).mockResolvedValue(OPTIONS)
  vi.mocked(api.getTimeline).mockResolvedValue({ events: [], pending_ocr: [], job: null })
})

describe('CaseDetail while OCR runs', () => {
  it('polls only the light status while nothing changes', async () => {
    vi.mocked(api.getCase).mockResolvedValue(caseWith(RUNNING, 'pending'))
    vi.mocked(api.getEvidenceNotes).mockResolvedValue(notes(['原證2 第1頁']))
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue(RUNNING)
    renderCase()
    await screen.findByText('辨識中 第 0／1 頁，約剩 1 分鐘')
    await waitFor(() => expect(vi.mocked(api.getDocumentOcrStatus).mock.calls.length).toBeGreaterThanOrEqual(3),
      { timeout: 3000 })
    expect(api.getCase).toHaveBeenCalledTimes(1)  // 沒有每 3 秒重讀整個案件
    expect(api.getEvidenceNotes).toHaveBeenCalledTimes(1)
    expect(api.getTimeline).toHaveBeenCalledTimes(1)
  })

  it('reloads the case once when OCR finishes, and the notes and timeline panels refetch', async () => {
    vi.mocked(api.getCase).mockResolvedValueOnce(caseWith(RUNNING, 'pending'))
      .mockResolvedValue(caseWith(FINISHED, 'done'))
    vi.mocked(api.getEvidenceNotes).mockResolvedValueOnce(notes(['原證2 第1頁'])).mockResolvedValue(notes([]))
    vi.mocked(api.getTimeline).mockResolvedValueOnce({ events: [], pending_ocr: ['原證2 第1頁'], job: null })
      .mockResolvedValue({ events: [], pending_ocr: [], job: null })
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue(FINISHED)
    renderCase()
    expect(await screen.findAllByText('原證2 第1頁待辨識，未處理')).toHaveLength(2)  // 卷證說明、大事記各一
    await waitFor(() => expect(screen.queryByText('原證2 第1頁待辨識，未處理')).not.toBeInTheDocument(),
      { timeout: 3000 })
    expect(api.getCase).toHaveBeenCalledTimes(2)  // 開頭一次、辨識結束一次
    expect(api.getEvidenceNotes).toHaveBeenCalledTimes(2)
    expect(api.getTimeline).toHaveBeenCalledTimes(2)
    await new Promise((r) => setTimeout(r, 80))
    expect(api.getCase).toHaveBeenCalledTimes(2)  // 結束後不再輪詢
  })
})

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import CaseDetail from './CaseDetail'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getCase: vi.fn(), getRound: vi.fn(), createRound: vi.fn(), runRound: vi.fn(), uploadDocument: vi.fn(),
    renumberExhibit: vi.fn() }
})

const baseCase: api.CaseDetail = {
  id: 'c1', title: '林大明車禍案', our_side: 'plaintiff', case_type: 'traffic_tort',
  documents: [
    { id: 'd1', kind: 'evidence', filename: '01_研判表.txt', round_id: null, page_count: 1,
      pages: [{ id: 'p1', page_no: 1, needs_ocr: false }] },
    { id: 'd2', kind: 'evidence', filename: '02_掃描收據.pdf', round_id: null, page_count: 1,
      pages: [{ id: 'p2', page_no: 1, needs_ocr: true }] },
  ],
  exhibits: [
    { id: 'e1', label: '原證1', prefix: '原證', number: 1, document_id: 'd1', filename: '01_研判表.txt' },
    { id: 'e2', label: '原證2', prefix: '原證', number: 2, document_id: 'd2', filename: '02_掃描收據.pdf' },
  ],
  rounds: [{ id: 'r1', seq: 1, status: 'failed', error: "usage_limit: You've hit your session limit" }],
}

function renderAt() {
  return render(
    <MemoryRouter initialEntries={['/cases/c1']}>
      <Routes><Route path="/cases/:caseId" element={<CaseDetail />} /></Routes>
    </MemoryRouter>,
  )
}

describe('CaseDetail', () => {
  beforeEach(() => vi.resetAllMocks())

  it('lists exhibits with numbers and marks scanned pages as 待辨識', async () => {
    vi.mocked(api.getCase).mockResolvedValue(baseCase)
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'failed', stale: false, draft_id: null,
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
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', stale: false, draft_id: 'dr1',
      error: null, claims: [] })
    renderAt()
    const link = await screen.findByRole('link', { name: '開啟草稿' })
    expect(link).toHaveAttribute('href', '/drafts/dr1')
  })
})

describe('CaseDetail references', () => {
  it('shows references the AI wanted but the system could not use', async () => {
    vi.mocked(api.getCase).mockResolvedValue({ ...baseCase, rounds: [{ id: 'r1', seq: 1, status: 'drafted', error: null }] })
    vi.mocked(api.getRound).mockResolvedValue({ id: 'r1', seq: 1, status: 'drafted', stale: false, draft_id: 'dr1',
      error: null, claims: [{ seq: 1, summary: '原告與有過失', quote: 'q', quote_verified: true, page_no: 1, references: [
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

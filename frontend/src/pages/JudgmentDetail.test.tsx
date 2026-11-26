import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { MeContext } from '../MeContext'
import JudgmentDetail from './JudgmentDetail'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, judgmentDetail: vi.fn(), requestSummary: vi.fn(), judgmentSummary: vi.fn(), pinPassage: vi.fn() }
})

const ME: api.Me = { id: 'm1', firm_id: 'f1', email: 'x@a.test', display_name: 'x', role: 'lawyer', bar_number: null,
  bar_verified: true }
const REASONING = '經查，上訴人所受傷害屬實。爰審酌一切情狀，認慰撫金20萬元尚稱允當。'
const NONE: api.SummaryView = { status: 'none', issues: [], excerpts: [], message: null }
const OK: api.SummaryView = { status: 'ok', issues: ['車禍慰撫金金額'], message: null,
  excerpts: [{ passage_seq: 2, text: '爰審酌一切情狀，認慰撫金20萬元尚稱允當。', span: [13, 35] }] }
const DETAIL: api.JudgmentDetailView = {
  id: 'j1', citation: '臺灣臺中地方法院113年度簡上字第211號民事判決', court: '臺灣臺中地方法院', decided_on: '2025-03-01',
  case_types: ['traffic_tort'], case_type_labels: ['車禍損害賠償'], annotation: null,
  source_url: 'https://judgment.judicial.gov.tw/x.pdf',
  articles: [{ display: '民法第195條', query: '民法第195條' }],
  cites: [{ key: '最高法院|51|台上|223', display: '最高法院51年度台上字第223號', library: [], citing_count: 7, supreme: true },
    { key: 'k2', display: '臺灣臺中地方法院112年度訴字第1號', library: [{ judgment_id: 'j9', citation: '臺中112訴1判決' }],
      citing_count: 1, supreme: false }],
  cited_by: [],
  passages: [
    { passage_id: 'p1', seq: 1, passage_type: 'party_claim', label: '當事人主張段', text: '上訴人主張如何。', pin_id: null },
    { passage_id: 'p2', seq: 2, passage_type: 'court_reasoning', label: '法院判斷段', text: REASONING, pin_id: null }],
  summary: NONE, supreme_importable: false,
}

function renderPage() {
  return render(
    <MeContext.Provider value={ME}>
      <MemoryRouter initialEntries={['/cases/c1/judgments/j1']}>
        <Routes>
          <Route path="/cases/:caseId/judgments/:judgmentId" element={<JudgmentDetail />} />
          <Route path="/cases/:caseId" element={<p>案件頁</p>} />
        </Routes>
      </MemoryRouter>
    </MeContext.Provider>)
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.judgmentDetail).mockResolvedValue(DETAIL)
  vi.mocked(api.requestSummary).mockResolvedValue(OK)
  vi.mocked(api.judgmentSummary).mockResolvedValue(OK)
})
afterEach(() => vi.useRealTimers())

describe('JudgmentDetail', () => {
  it('requests the summary once when there is none and shows it labelled as AI-written', async () => {
    renderPage()
    expect(await screen.findByText('車禍慰撫金金額')).toBeInTheDocument()
    expect(api.requestSummary).toHaveBeenCalledTimes(1)
    expect(api.requestSummary).toHaveBeenCalledWith('c1', 'j1', false)
    expect(screen.getByText('AI 撰寫')).toBeInTheDocument()
  })

  it('offers retry after a failure', async () => {
    vi.mocked(api.judgmentDetail).mockResolvedValue({ ...DETAIL, summary: { ...NONE, status: 'failed', message: '要旨產生失敗' } })
    renderPage()
    expect(await screen.findByText('要旨產生失敗')).toBeInTheDocument()
    expect(api.requestSummary).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(api.requestSummary).toHaveBeenCalledWith('c1', 'j1', true)
    expect(await screen.findByText('車禍慰撫金金額')).toBeInTheDocument()
  })

  it('polls until limit then offers retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const generating: api.SummaryView = { ...NONE, status: 'generating' }
    vi.mocked(api.judgmentDetail).mockResolvedValue({ ...DETAIL, summary: generating })
    vi.mocked(api.judgmentSummary).mockResolvedValueOnce({ ...NONE, status: 'stale' }).mockResolvedValue(generating)
    vi.mocked(api.requestSummary).mockResolvedValue(generating)
    renderPage()
    expect(await screen.findByText('要旨產生中…')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000) })
    await waitFor(() => expect(api.requestSummary).toHaveBeenCalledWith('c1', 'j1', false))  // 卡住的被接手
    for (let i = 0; i < 201; i++) {  // 每 3 秒一次、共 10 分鐘；每步讓畫面更新，下一次輪詢才會排上
      await act(async () => { await vi.advanceTimersByTimeAsync(3_000) })
    }
    expect(await screen.findByText('要旨產生時間過長')).toBeInTheDocument()
    const calls = vi.mocked(api.judgmentSummary).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(vi.mocked(api.judgmentSummary).mock.calls.length).toBe(calls)
    expect(screen.getByRole('button', { name: '重試' })).toBeInTheDocument()
  })

  it('highlights the excerpt in the full text using the span from the server', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: OK.excerpts[0].text }))
    const mark = document.querySelector('#passage-2 mark')
    expect(mark?.textContent).toBe(REASONING.slice(13, 35))
  })

  it('shows citations, the import hint and party-claim passages without pin buttons', async () => {
    renderPage()
    expect(await screen.findByText('目前無法加入判決庫')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '庫裡有 7 份判決引用' }))
      .toHaveAttribute('href', `/cases/c1/cited/${encodeURIComponent('最高法院|51|台上|223')}`)
    expect(screen.getByRole('link', { name: '臺中112訴1判決' })).toHaveAttribute('href', '/cases/c1/judgments/j9')
    expect(screen.getByText('庫裡還沒有判決引用這一份')).toBeInTheDocument()
    expect(screen.getByText('當事人主張，不是法院見解')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '釘選' })).toHaveLength(1)
    expect(screen.getByRole('link', { name: '司法院原文' })).toHaveAttribute('href', DETAIL.source_url)
    expect(screen.getByText(/2025-03-01・車禍損害賠償/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '回判決檢索' })).toHaveAttribute('href', '/cases/c1?tab=search')
  })

  it('pins a reasoning passage', async () => {
    vi.mocked(api.pinPassage).mockResolvedValue({} as api.PassageView)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '釘選' }))
    expect(api.pinPassage).toHaveBeenCalledWith('c1', 'p2', '')
  })

  it('searches by a cited article', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '民法第195條' }))
    expect(await screen.findByText('案件頁')).toBeInTheDocument()
  })
})

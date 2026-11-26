import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { MeContext } from '../MeContext'
import CitedJudgment from './CitedJudgment'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, citedJudgment: vi.fn() }
})

const ME: api.Me = { id: 'm1', firm_id: 'f1', email: 'x@a.test', display_name: 'x', role: 'lawyer', bar_number: null,
  bar_verified: true }
const KEY = '最高法院|51|台上|223'
const VIEW: api.CitedView = { key: KEY, display: '最高法院51年度台上字第223號', library: [], supreme_importable: true,
  citing: [{ judgment_id: 'j1', citation: '臺中113簡上211判決', sentence: '核定相當之數額（最高法院51年度台上字第223號判決意旨參照）。' }] }

function renderPage() {
  return render(
    <MeContext.Provider value={ME}>
      <MemoryRouter initialEntries={[`/cases/c1/cited/${encodeURIComponent(KEY)}`]}>
        <Routes>
          <Route path="/cases/:caseId/cited/:key" element={<CitedJudgment />} />
          <Route path="/cases/:caseId/judgments/:judgmentId" element={<p>整份判決頁</p>} />
        </Routes>
      </MemoryRouter>
    </MeContext.Provider>)
}

beforeEach(() => vi.resetAllMocks())

describe('CitedJudgment', () => {
  it('uses the same centred page width as the other case pages', async () => {
    vi.mocked(api.citedJudgment).mockResolvedValue(VIEW)
    renderPage()
    expect(await screen.findByText('最高法院51年度台上字第223號（不在判決庫）')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '回判決檢索' }).parentElement).toHaveClass('mx-auto', 'max-w-6xl', 'p-6')
  })

  it('lists the judgments citing an outside judgment with the import hint', async () => {
    vi.mocked(api.citedJudgment).mockResolvedValue(VIEW)
    renderPage()
    expect(await screen.findByText('最高法院51年度台上字第223號（不在判決庫）')).toBeInTheDocument()
    expect(api.citedJudgment).toHaveBeenCalledWith('c1', KEY)
    expect(screen.getByRole('link', { name: '臺中113簡上211判決' })).toHaveAttribute('href', '/cases/c1/judgments/j1')
    expect(screen.getByText(VIEW.citing[0].sentence)).toBeInTheDocument()
    expect(screen.getByText(/可以到司法院裁判書查詢找找看/)).toBeInTheDocument()
  })

  it('does not offer to import a criminal or unknown-court judgment', async () => {
    vi.mocked(api.citedJudgment).mockResolvedValue({ ...VIEW, key: '最高法院|100|台上|5|刑', display: '最高法院100年度台上字第5號（刑事）' })
    const { unmount } = renderPage()
    expect(await screen.findByText('刑事判決不能加入判決庫（判決庫只收民事、家事判決）')).toBeInTheDocument()
    expect(screen.queryByText(/可以到司法院裁判書查詢找找看/)).not.toBeInTheDocument()
    unmount()
    vi.mocked(api.citedJudgment).mockResolvedValue({ ...VIEW, key: '不明|101|上|2', display: '101年度上字第2號' })
    renderPage()
    expect(await screen.findByText(/法院不明，無法比對/)).toBeInTheDocument()
    expect(screen.queryByText(/可以到司法院裁判書查詢找找看/)).not.toBeInTheDocument()
  })

  it('opens the judgment directly when exactly one library judgment matches', async () => {
    vi.mocked(api.citedJudgment).mockResolvedValue({ ...VIEW, library: [{ judgment_id: 'j5', citation: '判決' }] })
    renderPage()
    expect(await screen.findByText('整份判決頁')).toBeInTheDocument()
  })

  it('lists both when two library judgments share the number', async () => {
    vi.mocked(api.citedJudgment).mockResolvedValue({ ...VIEW, library: [
      { judgment_id: 'j5', citation: '某判決' }, { judgment_id: 'j6', citation: '某裁定' }] })
    renderPage()
    expect(await screen.findByRole('link', { name: '某判決' })).toHaveAttribute('href', '/cases/c1/judgments/j5')
    expect(screen.getByRole('link', { name: '某裁定' })).toHaveAttribute('href', '/cases/c1/judgments/j6')
    expect(screen.queryByText(/不在判決庫/)).not.toBeInTheDocument()
    expect(screen.queryByText(/可以到司法院裁判書查詢找找看/)).not.toBeInTheDocument()
  })
})

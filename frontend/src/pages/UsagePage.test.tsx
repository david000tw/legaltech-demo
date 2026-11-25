import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { MeContext } from '../MeContext'
import UsagePage from './UsagePage'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getUsage: vi.fn(), getPrices: vi.fn(), postPrice: vi.fn(), putPrice: vi.fn(), putRate: vi.fn() }
})

const row = (over: Partial<api.UsageRow>): api.UsageRow => ({ source: 'bedrock', model: 'opus', step: 'draft', calls: 3,
  failed: 1, input: 1_000_000, output: 1_000_000, cache_read: 0, cache_write: 0, usd: 24, twd: 768, billing: 'metered',
  priced: true, ...over })
const SUMMARY: api.UsageSummary = {
  rows: [row({}), row({ source: 'claude_code_local', step: 'select', calls: 2, failed: 0, usd: 4, twd: 128,
    billing: 'unmetered' }), row({ source: 'fake', model: 'fake', step: 'claims', calls: 1, failed: 0, usd: null,
    twd: null, billing: null, priced: false })],
  totals: { metered: { usd: 24, twd: 768 }, unmetered: { usd: 4, twd: 128 } },
  notes: ['不按量計費，換算僅供參考', '未設定單價的來源不計入合計', '預設值，請依實際匯率修改'],
  rate: { usd_twd: 32, rate_set_on: '2026-11-24', is_default: true },
  filters: { months: ['2026-11', '2026-10'], steps: ['claims', 'draft', 'select'], sources: ['bedrock'], models: ['opus'],
    cases: [{ id: 'c1', title: '林大明車禍案' }] },
}
const PRICE: api.Price = { id: 'pr1', source: 'bedrock', model: 'opus', input_usd: 4, output_usd: 20, cache_read_usd: 0.2,
  cache_write_5m_usd: 5, cache_write_1h_usd: 8, billing: 'metered', note: '區域端點約加價 10%，請依實際帳單修改' }

function renderAs(role: string) {
  const me: api.Me = { id: 'm1', firm_id: 'f1', email: 'x@a.test', display_name: 'x', role, bar_number: null,
    bar_verified: role === 'lawyer' }
  return render(<MeContext.Provider value={me}><MemoryRouter><UsagePage /></MemoryRouter></MeContext.Provider>)
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getUsage).mockResolvedValue(SUMMARY)
  vi.mocked(api.getPrices).mockResolvedValue([PRICE])
  vi.mocked(api.putRate).mockResolvedValue({ usd_twd: 31.5, rate_set_on: '2026-11-20', is_default: false })
})

describe('UsagePage', () => {
  it('lists usage with separate unmetered total and unpriced rows', async () => {
    renderAs('lawyer')
    const table = await screen.findByRole('table', { name: '用量' })
    expect(within(table).getByText('3（失敗 1）')).toBeInTheDocument()
    expect(within(table).getByText('未設定單價')).toBeInTheDocument()
    expect(within(table).getByText('按量合計')).toBeInTheDocument()
    expect(within(table).getByText('不按量合計（不按量計費，換算僅供參考）')).toBeInTheDocument()
    expect(within(table).getAllByText('NT$768.00').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('未設定單價的來源不計入合計')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('月份'), '2026-11')
    await waitFor(() => expect(api.getUsage).toHaveBeenLastCalledWith({ month: '2026-11' }))
    await userEvent.selectOptions(screen.getByLabelText('案件'), 'c1')
    await waitFor(() => expect(api.getUsage).toHaveBeenLastCalledWith({ month: '2026-11', case_id: 'c1' }))
  })

  it('clears an earlier load error after a later load succeeds', async () => {
    vi.mocked(api.getUsage).mockResolvedValueOnce(SUMMARY).mockRejectedValueOnce(new api.ApiError(500, '用量讀取失敗'))
      .mockResolvedValue(SUMMARY)
    renderAs('lawyer')
    await screen.findByRole('table', { name: '用量' })
    await userEvent.selectOptions(screen.getByLabelText('月份'), '2026-11')
    expect(await screen.findByText('用量讀取失敗')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('月份'), '2026-10')
    await waitFor(() => expect(screen.queryByText('用量讀取失敗')).not.toBeInTheDocument())
  })

  it('labels OCR usage as 辨識', async () => {
    vi.mocked(api.getUsage).mockResolvedValue({ ...SUMMARY, rows: [row({ step: 'ocr' })],
      filters: { ...SUMMARY.filters, steps: ['ocr'] } })
    renderAs('lawyer')
    const table = await screen.findByRole('table', { name: '用量' })
    expect(within(table).getByText('辨識')).toBeInTheDocument()
  })

  it('firm admin edits a price and the exchange rate', async () => {
    vi.mocked(api.putPrice).mockResolvedValue({ ...PRICE, input_usd: 5 })
    renderAs('firm_admin')
    const prices = await screen.findByRole('table', { name: '單價' })
    await userEvent.click(within(prices).getByRole('button', { name: '修改 bedrock opus' }))
    const input = within(prices).getByLabelText('讀入（每百萬 token，美元）')
    await userEvent.clear(input)
    await userEvent.type(input, '5')
    await userEvent.click(within(prices).getByRole('button', { name: '儲存單價' }))
    expect(api.putPrice).toHaveBeenCalledWith('pr1', { input_usd: 5, output_usd: 20, cache_read_usd: 0.2,
      cache_write_5m_usd: 5, cache_write_1h_usd: 8, billing: 'metered', note: '區域端點約加價 10%，請依實際帳單修改' })
    expect(screen.getByRole('button', { name: '新增單價' })).toBeInTheDocument()
    await editRate()
  })

  it('lawyer edits the exchange rate but sees prices read-only', async () => {
    renderAs('lawyer')
    const prices = await screen.findByRole('table', { name: '單價' })
    expect(within(prices).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新增單價' })).not.toBeInTheDocument()
    expect(screen.getByText('單價限事務所管理員修改')).toBeInTheDocument()
    expect(screen.getByText('預設值，請依實際匯率修改', { selector: 'span' })).toBeInTheDocument()
    await editRate()
  })

  it('shows the 403 message for assistants', async () => {
    vi.mocked(api.getUsage).mockRejectedValue(new api.ApiError(403, '用量與費用頁限律師與事務所管理員'))
    vi.mocked(api.getPrices).mockRejectedValue(new api.ApiError(403, '用量與費用頁限律師與事務所管理員'))
    renderAs('assistant')
    expect(await screen.findByText('用量與費用頁限律師與事務所管理員')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

async function editRate() {
  const rate = screen.getByLabelText('匯率（1 美元兌新台幣）')
  expect(rate).toHaveValue(32)
  await userEvent.clear(rate)
  await userEvent.type(rate, '31.5')
  const day = screen.getByLabelText('匯率設定日期')
  await userEvent.clear(day)
  await userEvent.type(day, '2026-11-20')
  const before = vi.mocked(api.getUsage).mock.calls.length
  await userEvent.click(screen.getByRole('button', { name: '儲存匯率' }))
  expect(api.putRate).toHaveBeenCalledWith({ usd_twd: 31.5, rate_set_on: '2026-11-20' })
  await waitFor(() => expect(vi.mocked(api.getUsage).mock.calls.length).toBeGreaterThan(before))
}

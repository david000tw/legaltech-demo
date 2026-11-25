import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import TimelineSection from './TimelineSection'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getTimeline: vi.fn(), generateTimeline: vi.fn(), addTimelineEvent: vi.fn(),
    patchTimelineEvent: vi.fn(), deleteTimelineEvent: vi.fn(), getUncoveredDates: vi.fn() }
})

const ev = (over: Partial<api.TimelineEventView>): api.TimelineEventView => ({
  id: 'e1', date: '2025-03-09', date_label: '民國114年3月9日', description: '原告支付醫療費用', amount: 45000,
  source_kind: 'evidence', origin: 'ai',
  source: { page_id: 'p1', label: '原證1 第1頁', quote: '支付醫療費用新臺幣45,000元', status: 'found', reasons: [] },
  ...over,
})

beforeEach(() => vi.resetAllMocks())

describe('TimelineSection', () => {
  it('tags opponent events even after a lawyer edit', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [
      ev({ id: 'e2', description: '被告稱原告闖紅燈', source_kind: 'opponent', origin: 'lawyer',
        source: { page_id: 'p9', label: '對造書狀「答辯狀.txt」第1頁', quote: '原告闖紅燈', status: 'unchecked', reasons: [] } }),
    ], pending_ocr: [], job: null })
    render(<TimelineSection caseId="c1" />)
    const row = (await screen.findByText('被告稱原告闖紅燈')).closest('tr') as HTMLElement
    expect(within(row).getByText('對造主張').className).toMatch(/amber/)
    expect(within(row).getByText('律師')).toBeInTheDocument()
    expect(within(row).getByText('出處：對造書狀「答辯狀.txt」第1頁')).toBeInTheDocument()
  })

  it('accepts amounts written with 元 and commas, and rejects other text', async () => {
    // 最終審查：「45,000元」原本變成 NaN，送出時金額默默不見
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [], pending_ocr: [], job: null })
    vi.mocked(api.addTimelineEvent).mockResolvedValue(ev({ id: 'e3', origin: 'lawyer' }))
    render(<TimelineSection caseId="c1" />)
    await userEvent.type(await screen.findByLabelText('新增事件內容'), '原告支付車損')
    await userEvent.type(screen.getByLabelText('新增事件金額'), '新臺幣45,000元')
    await userEvent.click(screen.getByRole('button', { name: '新增事件' }))
    expect(api.addTimelineEvent).toHaveBeenCalledWith('c1', { date: null, description: '原告支付車損', amount: 45000 })
    await userEvent.type(screen.getByLabelText('新增事件內容'), '原告支付醫療費')
    await userEvent.type(screen.getByLabelText('新增事件金額'), '約四萬')
    await userEvent.click(screen.getByRole('button', { name: '新增事件' }))
    expect(await screen.findByText('金額請填數字，例如 45000 或 45,000元')).toBeInTheDocument()
    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1)
  })

  it('hints that ROC dates work in both date inputs', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], job: null })
    render(<TimelineSection caseId="c1" />)
    const add = await screen.findByLabelText('新增事件日期')
    expect(add).toHaveAttribute('placeholder', expect.stringContaining('114年3月9日'))
    await userEvent.click(within(screen.getByText('原告支付醫療費用').closest('tr') as HTMLElement)
      .getByRole('button', { name: '修改' }))
    expect(screen.getByLabelText('事件日期')).toHaveAttribute('placeholder', expect.stringContaining('114年3月9日'))
  })

  it('suggests refreshing when the sources changed after the last run', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], outdated: true, job: null })
    const { unmount } = render(<TimelineSection caseId="c1" />)
    expect(await screen.findByText('卷證或對造書狀已有變更，建議重新整理大事記')).toBeInTheDocument()
    unmount()
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], outdated: false, job: null })
    render(<TimelineSection caseId="c1" />)
    await screen.findByText('原告支付醫療費用')
    expect(screen.queryByText(/建議重新整理大事記/)).toBeNull()
  })

  it('does not show the refresh hint in the read-only compact view', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], outdated: true, job: null })
    render(<TimelineSection caseId="c1" compact />)
    await screen.findByText('原告支付醫療費用')
    expect(screen.queryByText(/建議重新整理大事記/)).toBeNull()
  })

  it('checks for dates in the evidence that the timeline leaves out', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], job: null })
    vi.mocked(api.getUncoveredDates).mockResolvedValue({ total: 2, dates: [
      { date: '2025-03-12', date_label: '民國114年3月12日', source_count: 1, sources: [
        { label: '原證2', page_no: 1, snippet: '114年3月12日 車輛修復：零件費110,000元', page_id: 'p2' }] },
      { date: '2025-04-02', date_label: '民國114年4月2日', source_count: 5, sources: [
        { label: '對造書狀A', page_no: 3, snippet: '被告於114年4月2日到庭', page_id: 'p9' }] },
    ] })
    render(<TimelineSection caseId="c1" />)
    await screen.findByText('原告支付醫療費用')
    expect(api.getUncoveredDates).not.toHaveBeenCalled()  // 只在按下按鈕時才檢查
    await userEvent.click(screen.getByRole('button', { name: '檢查漏列日期' }))
    expect(api.getUncoveredDates).toHaveBeenCalledWith('c1')
    expect(await screen.findByText('卷證中有 2 個日期沒有列入大事記')).toBeInTheDocument()
    const first = screen.getByText('民國114年3月12日').closest('li') as HTMLElement
    expect(first).toHaveTextContent('原證2 第1頁')
    expect(first).toHaveTextContent('114年3月12日 車輛修復：零件費110,000元')
    const second = screen.getByText('民國114年4月2日').closest('li') as HTMLElement
    expect(second).toHaveTextContent('對造書狀A 第3頁')
    expect(second).toHaveTextContent('共 5 處')
    expect(api.addTimelineEvent).not.toHaveBeenCalled()  // 只提示，不自動建立事件
  })

  it('says so when every date is already on the timeline, and reports a failed check', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], job: null })
    vi.mocked(api.getUncoveredDates).mockResolvedValueOnce({ total: 0, dates: [] })
      .mockRejectedValueOnce(new api.ApiError(500, '檢查失敗'))
    render(<TimelineSection caseId="c1" />)
    await screen.findByText('原告支付醫療費用')
    await userEvent.click(screen.getByRole('button', { name: '檢查漏列日期' }))
    expect(await screen.findByText('卷證裡的日期都已列入大事記')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '檢查漏列日期' }))
    expect(await screen.findByText('檢查失敗')).toBeInTheDocument()
    expect(screen.queryByText('卷證裡的日期都已列入大事記')).toBeNull()
  })

  it('has no uncovered-date check in the read-only compact view', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], job: null })
    render(<TimelineSection caseId="c1" compact />)
    await screen.findByText('原告支付醫療費用')
    expect(screen.queryByRole('button', { name: '檢查漏列日期' })).toBeNull()
  })

  it('adds, edits and deletes events', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [ev({})], pending_ocr: [], job: null })
    vi.mocked(api.addTimelineEvent).mockResolvedValue(ev({ id: 'e3', origin: 'lawyer' }))
    vi.mocked(api.patchTimelineEvent).mockResolvedValue(ev({ origin: 'lawyer' }))
    vi.mocked(api.deleteTimelineEvent).mockResolvedValue({ id: 'e1', deleted: true })
    render(<TimelineSection caseId="c1" />)
    await screen.findByText('原告支付醫療費用')
    await userEvent.type(screen.getByLabelText('新增事件日期'), '2025-03-20')
    await userEvent.type(screen.getByLabelText('新增事件內容'), '原告寄發存證信函')
    await userEvent.type(screen.getByLabelText('新增事件金額'), '1200')
    await userEvent.click(screen.getByRole('button', { name: '新增事件' }))
    expect(api.addTimelineEvent).toHaveBeenCalledWith('c1', { date: '2025-03-20', description: '原告寄發存證信函',
      amount: 1200 })
    const row = screen.getByText('原告支付醫療費用').closest('tr') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: '修改' }))
    const desc = within(row).getByLabelText('事件內容')
    await userEvent.clear(desc)
    await userEvent.type(desc, '原告支付醫療費')
    await userEvent.click(within(row).getByRole('button', { name: '儲存' }))
    expect(api.patchTimelineEvent).toHaveBeenCalledWith('e1', { date: '2025-03-09', description: '原告支付醫療費',
      amount: 45000 })
    await userEvent.click(within(screen.getByText('原告支付醫療費用').closest('tr') as HTMLElement)
      .getByRole('button', { name: '刪除' }))
    expect(api.deleteTimelineEvent).toHaveBeenCalledWith('e1')
  })

  it('renders ROC date labels and undated rows last', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [
      ev({}), ev({ id: 'e4', date: null, date_label: null, description: '日期不明的事件', amount: null }),
    ], pending_ocr: ['對造書狀A 第3頁'], job: null })
    render(<TimelineSection caseId="c1" />)
    await screen.findByText('日期不明的事件')
    const rows = screen.getAllByRole('row')
    expect(within(rows[1]).getByText('民國114年3月9日')).toBeInTheDocument()
    expect(within(rows[1]).getByText('新臺幣45,000元')).toBeInTheDocument()
    expect(within(rows[2]).getByText('（無日期）')).toBeInTheDocument()
    expect(screen.getByText('對造書狀A 第3頁待辨識，未處理')).toBeInTheDocument()
  })

  it('starts the timeline job and shows busy', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue({ events: [], pending_ocr: [], job: null })
    vi.mocked(api.generateTimeline).mockRejectedValue(new api.ApiError(409, 'AI 處理中，請稍候', 'busy'))
    render(<TimelineSection caseId="c1" />)
    await userEvent.click(await screen.findByRole('button', { name: '整理大事記' }))
    expect(api.generateTimeline).toHaveBeenCalledWith('c1')
    expect(await screen.findByText('AI 處理中，請稍候')).toBeInTheDocument()
  })
})

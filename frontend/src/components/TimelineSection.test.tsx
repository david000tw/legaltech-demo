import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import TimelineSection from './TimelineSection'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getTimeline: vi.fn(), generateTimeline: vi.fn(), addTimelineEvent: vi.fn(),
    patchTimelineEvent: vi.fn(), deleteTimelineEvent: vi.fn() }
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

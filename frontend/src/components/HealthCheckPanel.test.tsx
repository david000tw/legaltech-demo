import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import HealthCheckPanel from './HealthCheckPanel'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getHealthReport: vi.fn(), runHealthCheck: vi.fn() }
})

const found: api.SourceView = { page_id: 'p1', label: '原證1 第1頁', quote: '未讓幹道車先行', status: 'found', reasons: [] }
const item = (over: Partial<api.HealthItemView>): api.HealthItemView => ({
  level: 'high', title: '與有過失可能成立', explanation: '對造主張原告未減速。', claim_seqs: [1], absence: false,
  basis: [found], references: [{ code: 'J1', label: '臺灣臺中地方法院113年度中簡字第3946號民事判決' }],
  not_in_list: [], ...over,
})
const report = (over: Partial<api.HealthReportView>): api.HealthReportView => ({
  created_at: '2026-11-24T00:00:00Z', outdated: false,
  items: [item({}), item({ level: 'suggest', title: '補充事故現場照片', basis: [], references: [] }),
    item({ level: 'gap', title: '缺少行車紀錄器影像', absence: true, basis: [], references: [] })],
  undisputed: [{ fact: '被告承認原告未減速', basis: { ...found, label: '對造書狀「答辯狀.txt」第1頁' } }],
  issues: ['原告是否與有過失'], issues_label: '本件爭點', pending_ocr: [], ...over,
})
const job = (status: api.JobView['status'], error: string | null = null): api.JobView => ({
  id: 'j1', status, error, started_at: '2026-11-24T00:00:00Z', stale: false,
})

beforeEach(() => vi.resetAllMocks())

describe('HealthCheckPanel', () => {
  it('runs the check and polls until the report arrives', async () => {
    vi.mocked(api.getHealthReport)
      .mockResolvedValueOnce({ report: null, job: null })
      .mockResolvedValueOnce({ report: null, job: job('running') })
      .mockResolvedValue({ report: report({}), job: job('done') })
    vi.mocked(api.runHealthCheck).mockResolvedValue({ job: job('running') })
    render(<HealthCheckPanel roundId="r1" briefKind="preparatory" pollMs={20} />)
    await userEvent.click(await screen.findByRole('button', { name: '執行健檢' }))
    expect(api.runHealthCheck).toHaveBeenCalledWith('r1')
    expect(await screen.findByText('AI 處理中，請稍候')).toBeInTheDocument()
    expect(await screen.findByText('與有過失可能成立')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新執行健檢' })).toBeEnabled()
  })

  it('groups items by level with Chinese labels', async () => {
    vi.mocked(api.getHealthReport).mockResolvedValue({ report: report({}), job: job('done') })
    const { container } = render(<HealthCheckPanel roundId="r1" briefKind="preparatory" />)
    expect((await screen.findByText('高風險')).className).toMatch(/red/)
    expect(screen.getByText('補強建議').className).toMatch(/amber/)
    expect(screen.getByText('證據缺口').className).toMatch(/slate/)
    const high = screen.getByText('與有過失可能成立').closest('li') as HTMLElement
    expect(within(high).getByText('原文出處：原證1 第1頁')).toBeInTheDocument()
    expect(within(high).getByText('臺灣臺中地方法院113年度中簡字第3946號民事判決')).toBeInTheDocument()
    const gap = screen.getByText('缺少行車紀錄器影像').closest('li') as HTMLElement
    expect(within(gap).getByText('缺漏類，系統無法比對，請律師判斷')).toBeInTheDocument()
    expect(screen.getByText('兩造不爭執事項')).toBeInTheDocument()
    expect(screen.getByText('被告承認原告未減速')).toBeInTheDocument()
    expect(screen.getByText('本件爭點')).toBeInTheDocument()
    expect(screen.getByText('原告是否與有過失')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/已驗證|已證實/)
  })

  it('shows outdated banner', async () => {
    vi.mocked(api.getHealthReport).mockResolvedValue({ report: report({ outdated: true }), job: job('done') })
    render(<HealthCheckPanel roundId="r1" briefKind="preparatory" />)
    expect((await screen.findByText('資料已變更，建議重新執行')).className).toMatch(/yellow/)
  })

  it('complaint shows no undisputed facts', async () => {
    vi.mocked(api.getHealthReport).mockResolvedValue({
      report: report({ undisputed: null, issues_label: '預期爭點' }), job: job('done') })
    render(<HealthCheckPanel roundId="r1" briefKind="complaint" />)
    expect(await screen.findByText('起訴時尚無對造陳述')).toBeInTheDocument()
    expect(screen.getByText('預期爭點')).toBeInTheDocument()
  })

  it('shows not-in-list references', async () => {
    vi.mocked(api.getHealthReport).mockResolvedValue({
      report: report({ items: [item({ not_in_list: ['最高法院100年度台上字第1號'] })] }), job: job('done') })
    render(<HealthCheckPanel roundId="r1" briefKind="preparatory" />)
    expect(await screen.findByText('不在清單：最高法院100年度台上字第1號')).toBeInTheDocument()
  })

  it('shows the reason a check cannot start', async () => {
    vi.mocked(api.getHealthReport).mockResolvedValue({ report: null, job: job('failed', '卷證太多，超過一次健檢能處理的長度') })
    vi.mocked(api.runHealthCheck).mockRejectedValue(new api.ApiError(409, '起草完成後才能執行健檢', 'not_drafted'))
    render(<HealthCheckPanel roundId="r1" briefKind="preparatory" />)
    expect(await screen.findByText('卷證太多，超過一次健檢能處理的長度')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '執行健檢' }))
    expect(await screen.findByText('起草完成後才能執行健檢')).toBeInTheDocument()
  })
})

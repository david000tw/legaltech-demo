import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import JudgmentSearchTab from './JudgmentSearchTab'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, searchJudgments: vi.fn(), listPins: vi.fn(), pinPassage: vi.fn(), patchPin: vi.fn(),
    deletePin: vi.fn(), importJudgment: vi.fn() }
})

const P1: api.PassageView = { passage_id: 'jp1', citation: '臺灣臺中地方法院113年度中簡字第3946號民事判決',
  court: '臺灣臺中地方法院', decided_on: '2025-02-17',
  text: '本院審酌原告鎖骨骨折之傷勢，認原告請求慰撫金十萬元為適當。', annotation: null, pin_id: null, note: '' }
const URL = 'https://judgment.judicial.gov.tw/FILES/TNDV/114,訴,1347,20260113,1.pdf'

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.listPins).mockResolvedValue([])
})

async function search(q = '慰撫金 鎖骨骨折') {
  await userEvent.type(screen.getByLabelText('查詢字詞'), q)
  await userEvent.click(screen.getByRole('button', { name: '搜尋' }))
}

describe('JudgmentSearchTab', () => {
  it('searches with scope and highlights terms', async () => {
    vi.mocked(api.searchJudgments).mockResolvedValue({ results: [P1], terms: ['慰撫金', '鎖骨骨折'] })
    const { container } = render(<JudgmentSearchTab caseId="c1" />)
    await userEvent.selectOptions(await screen.findByLabelText('搜尋範圍'), 'all')
    await search()
    expect(api.searchJudgments).toHaveBeenCalledWith('c1', '慰撫金 鎖骨骨折', 'all')
    expect(await screen.findByText(P1.citation)).toBeInTheDocument()
    expect([...container.querySelectorAll('mark')].map((m) => m.textContent)).toEqual(['鎖骨骨折', '慰撫金'])
  })

  it('pins, edits the note and unpins', async () => {
    const pinned = { ...P1, pin_id: 'pin1' }
    vi.mocked(api.searchJudgments).mockResolvedValue({ results: [P1], terms: ['慰撫金'] })
    vi.mocked(api.pinPassage).mockResolvedValue(pinned)
    vi.mocked(api.patchPin).mockResolvedValue({ ...pinned, note: '過失比例' })
    vi.mocked(api.deletePin).mockResolvedValue(undefined)
    render(<JudgmentSearchTab caseId="c1" />)
    await search('慰撫金')
    vi.mocked(api.listPins).mockResolvedValue([pinned])
    await userEvent.click(await screen.findByRole('button', { name: '釘選' }))
    expect(api.pinPassage).toHaveBeenCalledWith('c1', 'jp1', '')
    const list = await screen.findByRole('list', { name: '已釘選' })
    expect(within(list).getByText(P1.citation)).toBeInTheDocument()
    expect(screen.getByText('已釘選', { selector: 'span' })).toBeInTheDocument()
    await userEvent.type(within(list).getByLabelText('備註'), '過失比例')
    await userEvent.click(within(list).getByRole('button', { name: '儲存備註' }))
    expect(api.patchPin).toHaveBeenCalledWith('pin1', '過失比例')
    vi.mocked(api.listPins).mockResolvedValue([])
    await userEvent.click(within(list).getByRole('button', { name: '取消釘選' }))
    expect(api.deletePin).toHaveBeenCalledWith('pin1')
    await waitFor(() => expect(screen.queryByRole('list', { name: '已釘選' })).not.toBeInTheDocument())
  })

  it('shows the minority-opinion note', async () => {
    vi.mocked(api.searchJudgments).mockResolvedValue({ results: [{ ...P1, annotation: '少數見解：否定配偶權' }],
      terms: ['慰撫金'] })
    render(<JudgmentSearchTab caseId="c1" />)
    await search('慰撫金')
    expect(await screen.findByText('少數見解：否定配偶權')).toBeInTheDocument()
  })

  it('imports a judgment by URL and lists its passages', async () => {
    vi.mocked(api.importJudgment)
      .mockRejectedValueOnce(new api.ApiError(422, '只接受司法院判決書系統的民事、家事判決網址', 'import_rejected'))
      .mockResolvedValueOnce({ judgment: { citation: '臺灣臺南地方法院114年度訴字第1347號民事判決', court: '臺灣臺南地方法院',
        decided_on: '2026-01-13' }, created: true, passages: [{ ...P1, passage_id: 'jp9' }] })
    vi.mocked(api.pinPassage).mockResolvedValue({ ...P1, passage_id: 'jp9', pin_id: 'pin9' })
    render(<JudgmentSearchTab caseId="c1" />)
    const box = await screen.findByLabelText('貼上司法院判決網址')
    await userEvent.type(box, 'https://evil.example/x.pdf')
    await userEvent.click(screen.getByRole('button', { name: '加入' }))
    expect(await screen.findByText('只接受司法院判決書系統的民事、家事判決網址')).toBeInTheDocument()
    await userEvent.clear(box)
    await userEvent.type(box, URL)
    await userEvent.click(screen.getByRole('button', { name: '加入' }))
    expect(api.importJudgment).toHaveBeenLastCalledWith('c1', URL)
    expect(await screen.findByText('已加入判決庫：臺灣臺南地方法院114年度訴字第1347號民事判決')).toBeInTheDocument()
    expect(screen.queryByText('只接受司法院判決書系統的民事、家事判決網址')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '釘選' }))
    expect(api.pinPassage).toHaveBeenCalledWith('c1', 'jp9', '')
  })
})

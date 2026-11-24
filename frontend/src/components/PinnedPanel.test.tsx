import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import PinnedPanel from './PinnedPanel'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, listPins: vi.fn(), insertPin: vi.fn() }
})

const TEXT = '本院審酌原告鎖骨骨折之傷勢，認原告請求慰撫金十萬元為適當。'
const PIN: api.PassageView = { passage_id: 'jp1', citation: '臺灣臺中地方法院113年度中簡字第3946號民事判決',
  court: '臺灣臺中地方法院', decided_on: '2025-02-17', text: TEXT, annotation: null, pin_id: 'pin1', note: '慰撫金行情' }
const PARAGRAPHS = [{ id: 'p1', no: 1, heading: '關於路權' }, { id: 'p2', no: 2, heading: '關於慰撫金' }]

function selectText(el: HTMLElement, start: number, end: number) {
  const node = el.firstChild as Text
  const range = document.createRange()
  range.setStart(node, start)
  range.setEnd(node, end)
  const sel = window.getSelection()!
  sel.removeAllRanges()
  sel.addRange(range)
  fireEvent.mouseUp(el)
}

beforeEach(() => vi.resetAllMocks())

describe('PinnedPanel', () => {
  it('inserts a pinned passage into the chosen paragraph', async () => {
    vi.mocked(api.listPins).mockResolvedValue([PIN])
    vi.mocked(api.insertPin).mockResolvedValue({ paragraph_id: 'p2', code: 'J3' })
    const onInserted = vi.fn()
    render(<PinnedPanel caseId="c1" paragraphs={PARAGRAPHS} onInserted={onInserted} />)
    expect(await screen.findByText(PIN.citation)).toBeInTheDocument()
    expect(screen.getByText('慰撫金行情')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '插入到第 1 段' })).toBeDisabled()  // 還沒選引文
    await userEvent.selectOptions(screen.getByLabelText('插入位置'), 'p2')
    selectText(screen.getByText(TEXT), 2, 22)
    const quote = TEXT.slice(2, 22)
    expect(screen.getByLabelText('引文（必填，至少 15 字）')).toHaveValue(quote)
    await userEvent.click(screen.getByRole('button', { name: '插入到第 2 段' }))
    expect(api.insertPin).toHaveBeenCalledWith('p2', 'pin1', quote)
    await waitFor(() => expect(onInserted).toHaveBeenCalled())
  })

  it('shows the server message when the quote is rejected', async () => {
    vi.mocked(api.listPins).mockResolvedValue([PIN])
    vi.mocked(api.insertPin).mockRejectedValue(new api.ApiError(422, '請在判決段落中選取至少 15 字的原文作為引文'))
    render(<PinnedPanel caseId="c1" paragraphs={PARAGRAPHS} onInserted={vi.fn()} />)
    await userEvent.type(await screen.findByLabelText('引文（必填，至少 15 字）'), '一二三四五六七八九十一二三四五')
    await userEvent.click(screen.getByRole('button', { name: '插入到第 1 段' }))
    expect(await screen.findByText('請在判決段落中選取至少 15 字的原文作為引文')).toBeInTheDocument()
  })

  it('says how to pin when nothing is pinned', async () => {
    vi.mocked(api.listPins).mockResolvedValue([])
    render(<PinnedPanel caseId="c1" paragraphs={PARAGRAPHS} onInserted={vi.fn()} />)
    expect(await screen.findByText('還沒有釘選的判決：請到案件頁的「判決檢索」分頁搜尋並釘選。')).toBeInTheDocument()
  })
})

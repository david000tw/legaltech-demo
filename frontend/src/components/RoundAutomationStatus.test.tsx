import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import RoundAutomationStatus from './RoundAutomationStatus'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, startDocumentOcr: vi.fn(), draftRound: vi.fn(), resetRound: vi.fn(), extractRound: vi.fn() }
})

const view = (over: Partial<api.AutomationView> = {}): api.AutomationView =>
  ({ mode: 'auto', auto_next: 'draft', running: null, wait: null, failed_stage: null, ...over })
const wait = (code: api.AutomationWait['code'], over: Partial<api.AutomationWait> = {}): api.AutomationWait =>
  ({ code, message: '等待中的原因', document_id: 'd1', page_id: 'pg1', hint: null, ...over })

function show(v: api.AutomationView) {
  const onAction = vi.fn(async (f: () => Promise<unknown>) => { await f() })
  const onOpenPage = vi.fn()
  render(<RoundAutomationStatus roundId="r1" automation={v} onAction={onAction} onOpenPage={onOpenPage} />)
  return { onAction, onOpenPage }
}

describe('RoundAutomationStatus', () => {
  beforeEach(() => vi.resetAllMocks())

  it('shows which step is running', () => {
    show(view({ running: 'draft' }))
    expect(screen.getByText('自動處理中：起草中（約 4 分鐘）')).toBeInTheDocument()
  })

  it('opens the problem page of the opponent brief', async () => {
    const { onOpenPage } = show(view({ auto_next: 'extract', wait: wait('opponent_ocr', { message: '對造書狀第 1 頁辨識失敗' }) }))
    expect(screen.getByText('等待中：對造書狀第 1 頁辨識失敗')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '看那一頁' }))
    expect(onOpenPage).toHaveBeenCalledWith('pg1')
  })

  it.each([
    ['retry_failed', '重新辨識失敗的頁', { retryFailed: true }],
    ['confirm_ocr', '確認辨識', { confirm: true }],
    ['resume_ocr', '繼續辨識', {}],
  ] as const)('evidence wait with %s offers %s', async (hint, label, opts) => {
    show(view({ wait: wait('evidence_ocr', { hint }) }))
    await userEvent.click(screen.getByRole('button', { name: label }))
    expect(api.startDocumentOcr).toHaveBeenCalledWith('d1', opts)
  })

  it('lets the lawyer draft without the pages still being read', async () => {
    show(view({ wait: wait('evidence_ocr', { hint: null }) }))
    await userEvent.click(screen.getByRole('button', { name: '先起草（不含還沒辨識的頁）' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1', { force: true })
  })

  it('offers re-extract or drafting without the new brief', async () => {
    show(view({ wait: wait('new_brief', { message: '有新上傳的書狀' }) }))
    await userEvent.click(screen.getByRole('button', { name: '重新拆解' }))
    expect(api.resetRound).toHaveBeenCalledWith('r1', true)
    expect(api.extractRound).toHaveBeenCalledWith('r1')
    await userEvent.click(screen.getByRole('button', { name: '不納入，直接起草' }))
    expect(api.draftRound).toHaveBeenCalledWith('r1', { ignoreNewBriefs: true })
  })

  it('only explains a rule that stopped automation', () => {
    show(view({ auto_next: null, wait: wait('blocked', { message: '沒有勾選任何主張' }) }))
    expect(screen.getByText('等待中：沒有勾選任何主張')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows nothing in manual mode or when idle', () => {
    const { container } = render(<RoundAutomationStatus roundId="r1" automation={view({ mode: 'manual' })}
      onAction={vi.fn()} onOpenPage={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})

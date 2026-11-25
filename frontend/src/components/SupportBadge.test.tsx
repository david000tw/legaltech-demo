import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import SupportBadge from './SupportBadge'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, releaseSupport: vi.fn(), retrySupport: vi.fn() }
})

const base: api.CitationView = { id: 'c1', code: 'J1', kind: 'judgment', quote: '未在適當位置暫停禮讓幹線道之系爭車輛',
  label: '臺中地院判決', verify_status: 'ok', verify_reasons: [], lawyer_confirmed: false, court_confirmed: false }
const done = (verdict: api.SupportVerdict, extra: Partial<api.SupportView> = {}): api.SupportView => ({
  status: 'done', verdict, explanation: '原文沒有提到闖紅燈', error: null, blocking: verdict !== 'supports',
  released: null, version: 'v1', ...extra })
const released = { name: 'lawyer', bar_number: '(110)北律字第0001號', at: '2026-11-25T03:00:00+00:00',
  reason: '原文第二段另有說明' }

function show(support: api.SupportView | null,
  opts: { canReview?: boolean; court?: boolean; busy?: boolean } = {}) {
  const onAction = vi.fn(async (f: () => Promise<unknown>) => { await f() })
  render(<SupportBadge citation={{ ...base, court_confirmed: opts.court ?? false, support }} draftId="d1"
    canReview={opts.canReview ?? true} busy={opts.busy} onAction={onAction} />)
  return onAction
}

const failed = (error: string): api.SupportView => ({ status: 'failed', verdict: null, explanation: null, error,
  blocking: true, released: null, version: 'v1' })

describe('SupportBadge', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it('shows nothing without a support result or when supported', () => {
    const { container } = render(<SupportBadge citation={base} draftId="d1" canReview onAction={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
    show(done('supports'))
    expect(screen.queryByText(/支持/)).toBeNull()
  })

  it('pending shows checking', () => {
    show({ status: 'pending', verdict: null, explanation: null, error: null, blocking: true, released: null })
    expect(screen.getByText('支持度檢查中')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it.each([
    ['usage_limit: x', '原因：Claude 用量額度已用完，額度恢復後再重新檢查，或填理由放行'],
    ['not_logged_in: x', '原因：本機 Claude Code 尚未登入'],
    ['timeout: x', '原因：AI 逾時，可以重新檢查'],
    ['invalid_output: 缺少結果', '原因：AI 回傳不完整，可以重新檢查'],
    ['other: 系統內部錯誤', '原因：系統內部錯誤'],
  ])('failed shows why it was not checked (%s)', (error, text) => {
    show(failed(error))
    expect(screen.getByText(text)).toBeInTheDocument()
  })

  it('buttons are disabled while another action is running', () => {
    show(failed('timeout: x'), { busy: true })
    expect(screen.getByRole('button', { name: '重新檢查' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '填理由放行' })).toBeDisabled()
  })

  it('failed offers retry for the whole draft and a lawyer release', async () => {
    show({ status: 'failed', verdict: null, explanation: null, error: 'usage_limit: 額度用完', blocking: true,
      released: null })
    expect(screen.getByText('支持度尚未檢查')).toBeInTheDocument()
    vi.mocked(api.retrySupport).mockResolvedValue({ queued: 3 })
    await userEvent.click(screen.getByRole('button', { name: '重新檢查' }))
    expect(api.retrySupport).toHaveBeenCalledWith('d1')
    expect(screen.getByRole('button', { name: '填理由放行' })).toBeInTheDocument()
  })

  it('not supported is red with the explanation and needs a reason', async () => {
    show(done('not_supported'))
    expect(screen.getByText('不支持')).toBeInTheDocument()
    expect(screen.getByText('原文沒有提到闖紅燈')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '填理由放行' }))
    const send = screen.getByRole('button', { name: '送出' })
    expect(send).toBeDisabled()
    await userEvent.type(screen.getByLabelText('放行理由'), '原文第二段另有說明')
    vi.mocked(api.releaseSupport).mockResolvedValue({} as api.ParagraphView)
    await userEvent.click(send)
    expect(api.releaseSupport).toHaveBeenCalledWith('c1', '原文第二段另有說明', 'v1')
  })

  it('party-claim misuse is red until the court reasoning was confirmed', () => {
    show(done('party_claim_misuse'))
    expect(screen.getByText('誤用當事人主張')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '填理由放行' })).toBeInTheDocument()
  })

  it('party-claim misuse after court confirmation is only a note', () => {
    show(done('party_claim_misuse', { blocking: false }), { court: true })
    expect(screen.getByText('AI 認為可能是當事人主張（已確認為法院見解）：原文沒有提到闖紅燈')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('partial is yellow and can be released without a reason', async () => {
    show(done('partial'))
    expect(screen.getByText('部分支持')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '確認放行' }))
    vi.mocked(api.releaseSupport).mockResolvedValue({} as api.ParagraphView)
    await userEvent.click(screen.getByRole('button', { name: '送出' }))
    expect(api.releaseSupport).toHaveBeenCalledWith('c1', '', 'v1')
  })

  it('released shows who released it, without buttons', () => {
    show(done('not_supported', { blocking: false, released }))
    expect(screen.getByText('已放行：lawyer（(110)北律字第0001號）2026-11-25：原文第二段另有說明')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows the release date in Taiwan time', () => {
    show(done('not_supported', { blocking: false, released: { ...released, at: '2026-11-24T17:36:00+00:00' } }))
    expect(screen.getByText('已放行：lawyer（(110)北律字第0001號）2026-11-25：原文第二段另有說明')).toBeInTheDocument()
  })

  it('a released unchecked citation says it was not checked by AI', () => {
    show({ status: 'failed', verdict: null, explanation: null, error: 'usage_limit: 額度用完', blocking: false,
      released: { ...released, reason: 'AI 無法使用' } })
    expect(screen.getByText('已放行（未經 AI 檢查）：lawyer（(110)北律字第0001號）2026-11-25：AI 無法使用'))
      .toBeInTheDocument()
  })

  it('assistants can retry but not release', () => {
    show({ status: 'failed', verdict: null, explanation: null, error: 'x', blocking: true, released: null },
      { canReview: false })
    expect(screen.getByRole('button', { name: '重新檢查' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '填理由放行' })).toBeNull()
    expect(screen.queryByRole('button', { name: '確認放行' })).toBeNull()
  })
})

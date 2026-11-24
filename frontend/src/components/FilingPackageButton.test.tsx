import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import FilingPackageButton from './FilingPackageButton'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, downloadFilingPackage: vi.fn() }
})

const BLOCKER: api.Blocker = { paragraph_seq: 1, paragraph_no: 1, reason: 'unreviewed', scope: 'paragraph' }

beforeEach(() => {
  vi.resetAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:pkg')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => vi.useRealTimers())

describe('FilingPackageButton', () => {
  it('downloads with the filename from the response', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.mocked(api.downloadFilingPackage).mockResolvedValue({ blob: new Blob(['zip']), filename: '民事起訴狀_遞狀包.zip' })
    const clicked: string[] = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download)
    })
    render(<FilingPackageButton draftId="dr1" blockers={[]} canReview />)
    await user.click(screen.getByRole('button', { name: '下載遞狀包' }))
    expect(api.downloadFilingPackage).toHaveBeenCalledWith('dr1')
    expect(clicked).toEqual(['民事起訴狀_遞狀包.zip'])
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(60_000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:pkg')
    click.mockRestore()
  })

  it('is disabled while blockers exist', () => {
    render(<FilingPackageButton draftId="dr1" blockers={[BLOCKER]} canReview />)
    expect(screen.getByRole('button', { name: '下載遞狀包' })).toBeDisabled()
  })

  it('is disabled for assistants with the reason', () => {
    render(<FilingPackageButton draftId="dr1" blockers={[]} canReview={false} />)
    expect(screen.getByRole('button', { name: '下載遞狀包' })).toBeDisabled()
    expect(screen.getByText('只有律師能下載遞狀包')).toBeInTheDocument()
  })

  it('shows blockers returned by a 409', async () => {
    vi.mocked(api.downloadFilingPackage).mockRejectedValue(new api.ExportBlockedError([BLOCKER]))
    render(<FilingPackageButton draftId="dr1" blockers={[]} canReview />)
    await userEvent.click(screen.getByRole('button', { name: '下載遞狀包' }))
    expect(await screen.findByText('第 1 段：段落尚未審核')).toBeInTheDocument()
  })
})

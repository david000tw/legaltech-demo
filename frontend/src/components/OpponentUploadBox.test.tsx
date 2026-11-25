import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import OpponentUploadBox from './OpponentUploadBox'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, patchCase: vi.fn(), uploadDocuments: vi.fn() }
})

const target = (over: Partial<api.OpponentTarget> = {}): api.OpponentTarget =>
  ({ round_id: null, seq: 2, new: true, brief_kind: 'preparatory', ...over })

function show(over: Partial<{ automation: api.AutomationMode; target: api.OpponentTarget }> = {}) {
  const onUploaded = vi.fn(async () => {})
  render(<OpponentUploadBox caseId="c1" ourSide="defendant" automation={over.automation ?? 'auto'}
    target={over.target ?? target()} onUploaded={onUploaded} onError={vi.fn()} />)
  return onUploaded
}

describe('OpponentUploadBox', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.uploadDocuments).mockResolvedValue([])
  })

  it('says where the upload goes', () => {
    show()
    expect(screen.getByText('會建立新的一輪（第 2 輪）')).toBeInTheDocument()
  })

  it('shows the existing round when the upload joins it, without a brief-kind choice', () => {
    show({ target: target({ round_id: 'r1', seq: 1, new: false }) })
    expect(screen.getByText('會放進：第 1 輪')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: '新一輪的書狀種類' })).not.toBeInTheDocument()
  })

  it('changes the automation mode of the case', async () => {
    vi.mocked(api.patchCase).mockResolvedValue({} as api.CaseSummary)
    const onUploaded = show()
    await userEvent.click(screen.getByRole('radio', { name: '拆完主張先停，讓我勾選' }))
    expect(api.patchCase).toHaveBeenCalledWith('c1', { automation: 'pause' })
    expect(onUploaded).toHaveBeenCalled()
  })

  it('uploads with the strategy and the chosen brief kind', async () => {
    const onUploaded = show()
    await userEvent.type(screen.getByRole('textbox', { name: '答辯策略（選填）' }), '主打原告與有過失')
    const file = new File(['%PDF'], '起訴狀.pdf', { type: 'application/pdf' })
    await userEvent.upload(screen.getByLabelText('上傳對方的書狀'), file)
    expect(api.uploadDocuments).toHaveBeenCalledWith('c1', [file], 'opponent_brief', undefined, undefined,
      { briefKind: 'preparatory', strategy: '主打原告與有過失' })
    expect(onUploaded).toHaveBeenCalled()
  })

  it('follows the brief kind of the next new round after an upload (審查 #7)', async () => {
    const props = { caseId: 'c1', ourSide: 'defendant' as const, automation: 'auto' as const,
      onUploaded: vi.fn(async () => {}), onError: vi.fn() }
    const { rerender } = render(<OpponentUploadBox {...props} target={target({ seq: 1, brief_kind: 'answer' })} />)
    expect(screen.getByRole('combobox', { name: '新一輪的書狀種類' })).toHaveValue('answer')
    rerender(<OpponentUploadBox {...props} target={target({ seq: 2, brief_kind: 'preparatory' })} />)
    expect(screen.getByRole('combobox', { name: '新一輪的書狀種類' })).toHaveValue('preparatory')
    const file = new File(['%PDF'], '準備書狀.pdf', { type: 'application/pdf' })
    await userEvent.upload(screen.getByLabelText('上傳對方的書狀'), file)
    expect(api.uploadDocuments).toHaveBeenCalledWith('c1', [file], 'opponent_brief', undefined, undefined,
      { briefKind: 'preparatory', strategy: '' })
  })

  it("keeps the lawyer's choice while the next round stays the same", async () => {
    const props = { caseId: 'c1', ourSide: 'defendant' as const, automation: 'auto' as const,
      onUploaded: vi.fn(async () => {}), onError: vi.fn() }
    const { rerender } = render(<OpponentUploadBox {...props} target={target({ seq: 2 })} />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '新一輪的書狀種類' }), 'answer')
    rerender(<OpponentUploadBox {...props} target={target({ seq: 2 })} />)
    expect(screen.getByRole('combobox', { name: '新一輪的書狀種類' })).toHaveValue('answer')
  })
})

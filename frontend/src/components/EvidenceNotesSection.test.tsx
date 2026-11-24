import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import EvidenceNotesSection from './EvidenceNotesSection'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getEvidenceNotes: vi.fn(), generateEvidenceNotes: vi.fn(), patchEvidenceNote: vi.fn(),
    deleteEvidenceNote: vi.fn() }
})

const found: api.SourceView = { page_id: 'p1', label: '原證1 第1頁', quote: '支付醫療費用新臺幣45,000元', status: 'found',
  reasons: [] }
const note = (over: Partial<api.EvidenceNoteView>): api.EvidenceNoteView => ({
  id: 'n1', kind: 'proves', text: '可證明原告支出醫療費用', origin: 'ai', source: found, ...over,
})
const doc = (over: Partial<api.EvidenceDocView>): api.EvidenceDocView => ({
  document_id: 'd1', exhibit: '原證1', filename: '01_收據.txt', needs_notes: false, pending_ocr: [],
  notes: [note({}), note({ id: 'n2', kind: 'caution', text: '收據未記載就診科別', source: { ...found, status: 'absence' } })],
  ...over,
})
const job = (status: api.JobView['status'], error: string | null = null): api.JobView => ({
  id: 'j1', status, error, started_at: '2026-11-24T00:00:00Z', stale: false,
})

beforeEach(() => vi.resetAllMocks())

describe('EvidenceNotesSection', () => {
  it('generates notes and polls until done', async () => {
    vi.mocked(api.getEvidenceNotes)
      .mockResolvedValueOnce({ documents: [doc({ notes: [], needs_notes: true })], job: null })
      .mockResolvedValueOnce({ documents: [doc({ notes: [], needs_notes: true })], job: job('running') })
      .mockResolvedValue({ documents: [doc({})], job: job('done') })
    vi.mocked(api.generateEvidenceNotes).mockResolvedValue({ job: job('running') })
    render(<EvidenceNotesSection caseId="c1" pollMs={20} />)
    await userEvent.click(await screen.findByRole('button', { name: '產生卷證說明' }))
    expect(api.generateEvidenceNotes).toHaveBeenCalledWith('c1')
    expect(await screen.findByText('AI 處理中，請稍候')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '產生卷證說明' })).toBeDisabled()
    expect(await screen.findByText('可證明原告支出醫療費用')).toBeInTheDocument()
    expect(screen.getByText('證明方向')).toBeInTheDocument()
    expect(screen.getByText('注意事項')).toBeInTheDocument()
    expect(screen.getByText('原文出處：原證1 第1頁')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '產生卷證說明' })).toBeEnabled()
  })

  it('shows pending scanned pages', async () => {
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({
      documents: [doc({ exhibit: '原證2', filename: '02_掃描.pdf', notes: [], pending_ocr: ['原證2 第1頁'] })], job: null })
    render(<EvidenceNotesSection caseId="c1" />)
    expect(await screen.findByText('原證2 第1頁待辨識，未處理')).toBeInTheDocument()
  })

  it('edits and deletes a note', async () => {
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [doc({})], job: job('done') })
    vi.mocked(api.patchEvidenceNote).mockResolvedValue(note({ text: '律師改寫', origin: 'lawyer' }))
    vi.mocked(api.deleteEvidenceNote).mockResolvedValue({ id: 'n2', deleted: true })
    render(<EvidenceNotesSection caseId="c1" />)
    const first = (await screen.findByText('可證明原告支出醫療費用')).closest('li') as HTMLElement
    await userEvent.click(within(first).getByRole('button', { name: '修改' }))
    const box = within(first).getByLabelText('卷證說明內容')
    await userEvent.clear(box)
    await userEvent.type(box, '律師改寫')
    await userEvent.click(within(first).getByRole('button', { name: '儲存' }))
    expect(api.patchEvidenceNote).toHaveBeenCalledWith('n1', '律師改寫')
    const second = screen.getByText('收據未記載就診科別').closest('li') as HTMLElement
    await userEvent.click(within(second).getByRole('button', { name: '刪除' }))
    expect(api.deleteEvidenceNote).toHaveBeenCalledWith('n2')
    await waitFor(() => expect(vi.mocked(api.getEvidenceNotes).mock.calls.length).toBeGreaterThanOrEqual(3))
  })

  it('shows the no_pending message', async () => {
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [doc({})], job: job('done') })
    vi.mocked(api.generateEvidenceNotes).mockRejectedValue(
      new api.ApiError(409, '所有卷證都已有說明，原文也沒有變更', 'no_pending'))
    render(<EvidenceNotesSection caseId="c1" />)
    await userEvent.click(await screen.findByRole('button', { name: '產生卷證說明' }))
    expect(await screen.findByText('所有卷證都已有說明，原文也沒有變更')).toBeInTheDocument()
  })

  it('shows the error of a failed job', async () => {
    vi.mocked(api.getEvidenceNotes).mockResolvedValue({ documents: [], job: job('failed', 'usage_limit: 已達用量上限') })
    render(<EvidenceNotesSection caseId="c1" />)
    expect(await screen.findByText('usage_limit: 已達用量上限')).toBeInTheDocument()
  })
})

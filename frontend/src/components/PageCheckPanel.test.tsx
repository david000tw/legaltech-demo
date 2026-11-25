import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import PageCheckPanel from './PageCheckPanel'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, getPageOcr: vi.fn(), savePageText: vi.fn(), verifyPage: vi.fn(), confirmBlank: vi.fn(),
    startDocumentOcr: vi.fn() }
})

const TEXT = '醫療費用合計新臺幣5,870元\n就診日期：民國114年2月30日'
const alert = (over: Partial<api.OcrAlert>): api.OcrAlert => ({ kind: 'mismatch', start: 13, end: 14,
  message: '校對員讀成「弊」，請對照原圖', proof_text: '弊', box: [10, 20, 110, 60], color: 'yellow', ...over })
const PAGE: api.PageOcr = {
  page_id: 'p1', document_id: 'd1', label: '原證2 第1頁', ocr_state: 'done', ocr_error: null, text: TEXT, version: 2,
  source: 'ai_ocr', needs_check: true, verified: null, has_image: true, can_edit: true, can_verify: true,
  can_save_verify: true, blank_unconfirmed: false,
  alerts: [alert({}), alert({ kind: 'bad_date', start: 21, end: 31, message: '日期不存在：民國114年2月30日', box: null,
    color: 'red', proof_text: null })],
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getPageOcr).mockResolvedValue(PAGE)
})

describe('PageCheckPanel', () => {
  it('lists red alerts first and highlights the span and box when clicked', async () => {
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    const items = await screen.findAllByRole('button', { name: /請對照原圖|日期不存在/ })
    expect(items.map((b) => b.textContent)).toEqual([
      expect.stringContaining('日期不存在'), expect.stringContaining('校對員讀成「弊」')])
    expect(screen.getByRole('img', { name: '原證2 第1頁 原圖' })).toHaveAttribute('src', '/api/pages/p1/image')
    await userEvent.click(items[1])
    const box = screen.getByRole('textbox', { name: '辨識文字' }) as HTMLTextAreaElement
    expect([box.selectionStart, box.selectionEnd]).toEqual([13, 14])
    expect(screen.getByTestId('alert-box')).toBeInTheDocument()
    await userEvent.click(items[0])
    expect([box.selectionStart, box.selectionEnd]).toEqual([21, 31])
    expect(screen.queryByTestId('alert-box')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '放大' }))
    await userEvent.click(screen.getByRole('button', { name: '縮小' }))
  })

  it('saves text with the base version and shows conflict message on 409', async () => {
    const onChanged = vi.fn()
    vi.mocked(api.savePageText).mockResolvedValueOnce({ ...PAGE, version: 3, text: TEXT + '整' })
    render(<PageCheckPanel pageId="p1" canReview={false} onChanged={onChanged} />)
    const box = await screen.findByRole('textbox', { name: '辨識文字' })
    fireEvent.change(box, { target: { value: TEXT + '整' } })
    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(api.savePageText).toHaveBeenCalledWith('p1', TEXT + '整', 2, false)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    vi.mocked(api.savePageText).mockRejectedValueOnce(new api.ApiError(409, '這一頁的文字已更新，請重新載入'))
    fireEvent.change(box, { target: { value: TEXT + '整。' } })
    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(await screen.findByText('這一頁的文字已更新，請重新載入')).toBeInTheDocument()
    expect(api.getPageOcr).toHaveBeenCalledTimes(2)
  })

  it('lawyer can verify; assistant sees no verify buttons', async () => {
    vi.mocked(api.verifyPage).mockResolvedValue({ ...PAGE, needs_check: false, can_verify: false,
      verified: { by_name: '王律師', at: '2026-11-24T10:00:00+00:00' } })
    const { unmount } = render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: '已對照原圖' }))
    expect(api.verifyPage).toHaveBeenCalledWith('p1', 2)
    expect(await screen.findByText(/王律師 已對照原圖/)).toBeInTheDocument()
    expect(screen.getAllByText('已對照').length).toBe(2)
    unmount()
    render(<PageCheckPanel pageId="p1" canReview={false} onChanged={vi.fn()} />)
    await screen.findByRole('textbox', { name: '辨識文字' })
    expect(screen.queryByRole('button', { name: '已對照原圖' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '儲存並標為已對照' })).not.toBeInTheDocument()
  })

  it('unsaved edits disable 已對照原圖 so the old text is not certified', async () => {
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    const box = await screen.findByRole('textbox', { name: '辨識文字' })
    const verify = screen.getByRole('button', { name: '已對照原圖' })
    expect(verify).toBeEnabled()
    fireEvent.change(box, { target: { value: TEXT.replace('5,870', '5,780') } })
    expect(verify).toBeDisabled()
    expect(screen.getByText('有未儲存的修改：請按「儲存並標為已對照」')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '儲存並標為已對照' })).toBeEnabled()
    fireEvent.change(box, { target: { value: TEXT } })
    expect(verify).toBeEnabled()
  })

  it('save buttons stay disabled until the text changes (no new version for an unchanged page)', async () => {
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    const box = await screen.findByRole('textbox', { name: '辨識文字' })
    expect(screen.getByRole('button', { name: '儲存' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '儲存並標為已對照' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '已對照原圖' })).toBeEnabled()
    fireEvent.change(box, { target: { value: TEXT + '整' } })
    expect(screen.getByRole('button', { name: '儲存' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '儲存並標為已對照' })).toBeEnabled()
  })

  it('failed page: typed text can be saved and marked verified in one step', async () => {
    vi.mocked(api.getPageOcr).mockResolvedValue({ ...PAGE, ocr_state: 'failed', ocr_error: 'refusal: 模型拒絕回答',
      text: null, version: null, needs_check: false, can_verify: false, alerts: [] })
    vi.mocked(api.savePageText).mockResolvedValue({ ...PAGE, version: 1, needs_check: false })
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    const box = await screen.findByRole('textbox', { name: '辨識文字' })
    expect(screen.getByRole('button', { name: '儲存並標為已對照' })).toBeDisabled()  // 還沒輸入
    expect(screen.getByRole('button', { name: '已對照原圖' })).toBeDisabled()  // 沒有版本可勾
    fireEvent.change(box, { target: { value: '手動輸入的內容' } })
    expect(screen.getByText('有未儲存的修改：請按「儲存並標為已對照」')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '儲存並標為已對照' }))
    expect(api.savePageText).toHaveBeenCalledWith('p1', '手動輸入的內容', null, true)
  })

  it('already verified page: an edit can be saved and verified again in one step', async () => {
    vi.mocked(api.getPageOcr).mockResolvedValue({ ...PAGE, needs_check: false, can_verify: false,
      verified: { by_name: '王律師', at: '2026-11-24T10:00:00+00:00' } })
    vi.mocked(api.savePageText).mockResolvedValue({ ...PAGE, version: 3, needs_check: false })
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    const box = await screen.findByRole('textbox', { name: '辨識文字' })
    expect(screen.getByRole('button', { name: '已對照原圖' })).toBeDisabled()  // 已經對照過，不用再勾
    fireEvent.change(box, { target: { value: TEXT + '整' } })
    await userEvent.click(screen.getByRole('button', { name: '儲存並標為已對照' }))
    expect(api.savePageText).toHaveBeenCalledWith('p1', TEXT + '整', 2, true)
  })

  it('save-and-verify stays disabled when the server says it is not allowed (proofreader still comparing)', async () => {
    vi.mocked(api.getPageOcr).mockResolvedValue({ ...PAGE, ocr_state: 'read', can_verify: false,
      can_save_verify: false })
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    fireEvent.change(await screen.findByRole('textbox', { name: '辨識文字' }), { target: { value: TEXT + '整' } })
    expect(screen.getByRole('button', { name: '儲存並標為已對照' })).toBeDisabled()
    expect(screen.getByText('有未儲存的修改：請先按「儲存」，再對照原圖')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '儲存' })).toBeEnabled()
  })

  it('verify disabled when can_verify is false', async () => {
    vi.mocked(api.getPageOcr).mockResolvedValue({ ...PAGE, ocr_state: 'read', can_verify: false,
      can_save_verify: false })
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    expect(await screen.findByRole('button', { name: '已對照原圖' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '儲存並標為已對照' })).toBeDisabled()
  })

  it('failed page shows error, retry and manual input', async () => {
    vi.mocked(api.getPageOcr).mockResolvedValue({ ...PAGE, ocr_state: 'failed', ocr_error: 'refusal: 模型拒絕回答',
      text: null, version: null, needs_check: false, can_verify: false, alerts: [] })
    vi.mocked(api.startDocumentOcr).mockResolvedValue({ job: null, progress: { total: 1, done: 0, failed: 0,
      proofing: 0, minutes_left: 1 } })
    vi.mocked(api.savePageText).mockResolvedValue({ ...PAGE, version: 1 })
    render(<PageCheckPanel pageId="p1" canReview onChanged={vi.fn()} />)
    expect(await screen.findByText('refusal: 模型拒絕回答')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '重新辨識' }))
    expect(api.startDocumentOcr).toHaveBeenCalledWith('d1', { retryFailed: true })
    fireEvent.change(screen.getByRole('textbox', { name: '辨識文字' }), { target: { value: '手動輸入的內容' } })
    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(api.savePageText).toHaveBeenCalledWith('p1', '手動輸入的內容', null, false)
  })

  it('blank page can be confirmed', async () => {
    vi.mocked(api.getPageOcr).mockResolvedValue({ ...PAGE, text: '', blank_unconfirmed: true,
      alerts: [alert({ kind: 'blank', start: 0, end: 0, message: '空白頁？請確認', box: null })] })
    vi.mocked(api.confirmBlank).mockResolvedValue({ ...PAGE, text: '', blank_unconfirmed: false })
    const onChanged = vi.fn()
    render(<PageCheckPanel pageId="p1" canReview={false} onChanged={onChanged} />)
    await userEvent.click(await screen.findByRole('button', { name: '確認是空白頁' }))
    expect(api.confirmBlank).toHaveBeenCalledWith('p1')
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '確認是空白頁' })).not.toBeInTheDocument()
  })
})

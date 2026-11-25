import { act, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import OcrStatus from './OcrStatus'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, startDocumentOcr: vi.fn(), reocrDocument: vi.fn(), getDocumentOcrStatus: vi.fn() }
})

const job = (status: api.OcrJob['status']): api.OcrJob => ({ id: 'j1', status, error: null,
  started_at: '2026-11-24T10:00:00+00:00', stale: false, document_id: 'd1' })
const page = (id: string, no: number, over: Partial<api.DocPage> = {}): api.DocPage => ({ id, page_no: no,
  needs_ocr: true, ocr_state: 'done', needs_check: true, alert_count: 0, verified: false, ...over })

function doc(ocr: Partial<api.DocOcr>, pages: api.DocPage[] = [page('p1', 1)]): api.CaseDocument {
  return { id: 'd1', kind: 'evidence', filename: '02_掃描收據.pdf', round_id: null, filed_by_party_id: null,
    page_count: pages.length, pages,
    ocr: { job: null, progress: { total: pages.length, done: 0, failed: 0, proofing: 0, minutes_left: 1 },
      can_reocr: false, scan_hint: false, can_resume: false, ...ocr } }
}

const OK = { job: job('running'), progress: { total: 5, done: 0, failed: 0, proofing: 0, minutes_left: 2 } }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.startDocumentOcr).mockResolvedValue(OK)
  vi.mocked(api.reocrDocument).mockResolvedValue({ ...OK, pages: 1 })
  vi.mocked(api.getDocumentOcrStatus).mockResolvedValue({ ...OK, can_resume: false })
})
afterEach(() => vi.restoreAllMocks())

describe('OcrStatus', () => {
  it('shows progress with minutes left', async () => {
    const onChanged = vi.fn(async () => {})
    const pages = [page('p1', 1), page('p2', 2, { ocr_state: 'read' }), page('p3', 3, { ocr_state: 'pending' }),
      page('p4', 4, { ocr_state: 'pending' }), page('p5', 5, { ocr_state: 'failed' })]
    const onOpenPage = vi.fn()
    render(<OcrStatus doc={doc({ job: job('running'), progress: { total: 5, done: 1, failed: 1, proofing: 1,
      minutes_left: 3 } }, pages)} onChanged={onChanged} onOpenPage={onOpenPage} pollMs={20} />)
    expect(screen.getByText('辨識中 第 2／5 頁，約剩 3 分鐘')).toBeInTheDocument()
    expect(screen.getAllByText('辨識中').length).toBe(2)  // 兩頁待辨識、工作在跑
    expect(screen.getByText('校對中')).toBeInTheDocument()
    expect(screen.getByText('辨識失敗')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '第1頁：已辨識（0 則提醒）' }))
    expect(onOpenPage).toHaveBeenCalledWith('p1')
    await waitFor(() => expect(api.getDocumentOcrStatus).toHaveBeenCalledWith('d1'))  // 工作在跑時輪詢輕量狀態
    expect(screen.queryByRole('button', { name: '重新辨識' })).not.toBeInTheDocument()
  })

  it('polls the light status and reloads the case only when the OCR state changes', async () => {
    const running = { job: job('running'), progress: { total: 5, done: 1, failed: 0, proofing: 0, minutes_left: 3 },
      can_resume: false }
    const next = { ...running, progress: { ...running.progress, done: 2 } }  // 又讀完一頁
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue(running)  // 跟畫面上一樣：沒有變
    const onChanged = vi.fn()
    const pages = [page('p1', 1), page('p2', 2, { ocr_state: 'pending' })]
    // 上層重讀後會把新狀態傳回來（跟 CaseDetail 一樣）
    function Harness() {
      const [d, setD] = useState(doc(running, pages))
      return <OcrStatus doc={d} onOpenPage={vi.fn()} pollMs={15}
        onChanged={async () => { onChanged(); setD(doc(next, pages)) }} />
    }
    render(<Harness />)
    await waitFor(() => expect(vi.mocked(api.getDocumentOcrStatus).mock.calls.length).toBeGreaterThanOrEqual(3),
      { timeout: 3000 })
    expect(onChanged).not.toHaveBeenCalled()  // 輪詢本身不重讀整個案件
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue(next)
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const calls = vi.mocked(api.getDocumentOcrStatus).mock.calls.length
    await waitFor(() => expect(vi.mocked(api.getDocumentOcrStatus).mock.calls.length).toBeGreaterThan(calls + 2),
      { timeout: 3000 })
    expect(onChanged).toHaveBeenCalledTimes(1)  // 畫面已經是新狀態：之後的輪詢不再重讀
  })

  it('ignores the drifting minutes-left estimate (it grows with the clock, not with the state)', async () => {
    const running = { job: job('running'), progress: { total: 30, done: 1, failed: 0, proofing: 0, minutes_left: 3 },
      can_resume: false }
    let minutes = 3
    vi.mocked(api.getDocumentOcrStatus).mockImplementation(async () => ({ ...running,
      progress: { ...running.progress, minutes_left: ++minutes } }))
    const onChanged = vi.fn(async () => {})
    render(<OcrStatus doc={doc(running, [page('p1', 1), page('p2', 2, { ocr_state: 'pending' })])}
      onChanged={onChanged} onOpenPage={vi.fn()} pollMs={15} />)
    await waitFor(() => expect(vi.mocked(api.getDocumentOcrStatus).mock.calls.length).toBeGreaterThanOrEqual(4),
      { timeout: 3000 })
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('retries on the next poll when the reload did not bring the screen up to date', async () => {
    const running = { job: job('running'), progress: { total: 5, done: 1, failed: 0, proofing: 0, minutes_left: 3 },
      can_resume: false }
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue({ ...running,
      progress: { ...running.progress, done: 2 } })
    const onChanged = vi.fn(async () => {})  // 重讀失敗：畫面沒變
    render(<OcrStatus doc={doc(running, [page('p1', 1), page('p2', 2, { ocr_state: 'pending' })])}
      onChanged={onChanged} onOpenPage={vi.fn()} pollMs={15} />)
    await waitFor(() => expect(onChanged.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 3000 })
  })

  it('does not poll when nothing is running', async () => {
    render(<OcrStatus doc={doc({ job: job('done') }, [page('p1', 1)])} onChanged={vi.fn(async () => {})}
      onOpenPage={vi.fn()} pollMs={15} />)
    await new Promise((r) => setTimeout(r, 80))
    expect(api.getDocumentOcrStatus).not.toHaveBeenCalled()
  })

  it('reloads when the job finishes', async () => {
    const onChanged = vi.fn(async () => {})
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue({ job: job('done'),
      progress: { total: 1, done: 1, failed: 0, proofing: 0, minutes_left: 0 }, can_resume: false })
    render(<OcrStatus doc={doc({ job: job('running') }, [page('p1', 1, { ocr_state: 'pending' })])}
      onChanged={onChanged} onOpenPage={vi.fn()} pollMs={15} />)
    await waitFor(() => expect(onChanged).toHaveBeenCalled(), { timeout: 3000 })
  })

  it('waiting asks to start and confirms over the limit', async () => {
    const message = '這次要辨識 60 頁掃描頁，預估約 20 分鐘，要開始辨識嗎？'
    vi.mocked(api.startDocumentOcr).mockRejectedValueOnce(new api.ApiError(409, message, 'confirm_ocr'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChanged = vi.fn(async () => {})
    const pages = Array.from({ length: 3 }, (_, i) => page(`p${i}`, i + 1, { ocr_state: 'pending' }))
    render(<OcrStatus doc={doc({ job: job('waiting') }, pages)} onChanged={onChanged} onOpenPage={vi.fn()} />)
    expect(screen.getAllByText('等待確認').length).toBe(3)
    await userEvent.click(screen.getByRole('button', { name: '開始辨識（3 頁）' }))
    expect(confirm).toHaveBeenCalledWith(message)
    expect(api.startDocumentOcr).toHaveBeenNthCalledWith(1, 'd1', {})
    expect(api.startDocumentOcr).toHaveBeenNthCalledWith(2, 'd1', { confirm: true })
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('does not start when the confirmation is declined', async () => {
    vi.mocked(api.startDocumentOcr).mockRejectedValueOnce(new api.ApiError(409, '要開始辨識嗎？', 'confirm_ocr'))
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<OcrStatus doc={doc({ job: job('waiting') }, [page('p1', 1, { ocr_state: 'pending' })])}
      onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '開始辨識（1 頁）' }))
    expect(api.startDocumentOcr).toHaveBeenCalledTimes(1)
  })

  it('shows why OCR stopped (usage limit, not logged in, other)', () => {
    const failed = (error: string) => doc({ job: { ...job('failed'), error }, can_resume: true },
      [page('p1', 1, { ocr_state: 'pending' })])
    const { rerender } = render(<OcrStatus doc={failed('usage_limit: Claude AI usage limit reached|1764000000')}
      onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('辨識已停下：Claude 用量額度已用完')
    expect(screen.getByRole('button', { name: '繼續辨識' })).toBeInTheDocument()
    rerender(<OcrStatus doc={failed('not_logged_in: Please run /login')} onChanged={vi.fn(async () => {})}
      onOpenPage={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('辨識已停下：本機 Claude Code 尚未登入')
    rerender(<OcrStatus doc={failed('other: 系統內部錯誤，請查看伺服器紀錄後重跑')} onChanged={vi.fn(async () => {})}
      onOpenPage={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('辨識已停下：系統內部錯誤，請查看伺服器紀錄後重跑')
  })

  it('drops the stop reason once nothing is left to resume (pages typed in by hand)', () => {
    render(<OcrStatus doc={doc({ job: { ...job('failed'), error: 'usage_limit: Claude AI usage limit reached' },
      can_resume: false }, [page('p1', 1)])} onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('resume button when can_resume', async () => {
    render(<OcrStatus doc={doc({ can_resume: true, job: { ...job('running'), stale: true } },
      [page('p1', 1, { ocr_state: 'pending' })])} onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    expect(screen.getByText('待辨識')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '繼續辨識' }))
    expect(api.startDocumentOcr).toHaveBeenCalledWith('d1', {})
  })

  it('retry failed pages', async () => {
    render(<OcrStatus doc={doc({ job: job('done'), progress: { total: 2, done: 1, failed: 1, proofing: 0,
      minutes_left: 0 } }, [page('p1', 1), page('p2', 2, { ocr_state: 'failed' })])}
      onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '重新辨識' }))
    expect(api.startDocumentOcr).toHaveBeenCalledWith('d1', { retryFailed: true })
  })

  it('reocr with scan hint', async () => {
    render(<OcrStatus doc={doc({ can_reocr: true, scan_hint: true, progress: { total: 0, done: 0, failed: 0,
      proofing: 0, minutes_left: 0 } }, [page('p1', 1, { ocr_state: 'none', needs_ocr: false, needs_check: false })])}
      onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    expect(screen.getByText('這份看起來是掃描後加上文字的 PDF，建議改用辨識')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /第1頁/ })).not.toBeInTheDocument()  // 文字層頁不顯示狀態
    await userEvent.click(screen.getByRole('button', { name: '改用辨識' }))
    expect(api.reocrDocument).toHaveBeenCalledWith('d1', false)
  })

  it('verified page shows 已對照原圖', () => {
    render(<OcrStatus doc={doc({}, [page('p1', 1, { needs_check: false, verified: true, alert_count: 2 })])}
      onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()} />)
    expect(screen.getByRole('button', { name: '第1頁：已對照原圖' })).toBeInTheDocument()
  })
})

// 輪詢的時間點用假時鐘控制：電腦忙的時候，真實時鐘下這兩種情況都會偶發
describe('OcrStatus polling timing', () => {
  afterEach(() => vi.useRealTimers())
  const running = { job: job('running'), progress: { total: 5, done: 1, failed: 0, proofing: 0, minutes_left: 3 },
    can_resume: false }
  const pages = [page('p1', 1), page('p2', 2, { ocr_state: 'pending' })]

  it('does not reload again before the screen has caught up with the last reload', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getDocumentOcrStatus).mockResolvedValue({ ...running, progress: { ...running.progress, done: 2 } })
    const onChanged = vi.fn(async () => {})  // 重讀完成了，但畫面還沒換成新資料
    render(<OcrStatus doc={doc(running, pages)} onChanged={onChanged} onOpenPage={vi.fn()} pollMs={15} />)
    await act(() => vi.advanceTimersByTimeAsync(15))
    expect(onChanged).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(15))
    expect(onChanged).toHaveBeenCalledTimes(1)  // 緊接著的這次輪詢先等畫面更新，不重複重讀
    await act(() => vi.advanceTimersByTimeAsync(15))
    expect(onChanged).toHaveBeenCalledTimes(2)  // 畫面一直沒更新（重讀失敗）：下一輪仍會再試
  })

  it('does not send another status request while the last one is still pending', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getDocumentOcrStatus).mockReturnValue(new Promise(() => {}))  // 後端很慢，一直沒回
    render(<OcrStatus doc={doc(running, pages)} onChanged={vi.fn(async () => {})} onOpenPage={vi.fn()}
      pollMs={15} />)
    await act(() => vi.advanceTimersByTimeAsync(60))
    expect(api.getDocumentOcrStatus).toHaveBeenCalledTimes(1)
  })
})

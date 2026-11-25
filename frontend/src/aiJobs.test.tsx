import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useJobPolling } from './aiJobs'

describe('useJobPolling', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('skips a tick while the previous refresh has not come back yet', async () => {
    // 後端慢或正在匯出時，上一次讀取還沒回來就不要再送，避免請求越積越多、占滿資料庫連線
    let finish: () => void = () => {}
    const refresh = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    renderHook(() => useJobPolling(true, refresh, 1000))

    await act(async () => { vi.advanceTimersByTime(3500) })
    expect(refresh).toHaveBeenCalledTimes(1)

    await act(async () => { finish() })
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('keeps polling after a refresh fails', async () => {
    const refresh = vi.fn(() => Promise.reject(new Error('network')))
    renderHook(() => useJobPolling(true, refresh, 1000))
    await act(async () => { vi.advanceTimersByTime(1000) })
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(refresh).toHaveBeenCalledTimes(2)
  })
})

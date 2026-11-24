import { useState } from 'react'
import * as api from '../api'
import { BTN_SECONDARY } from '../ui'
import { blockerPlace, reasonLabel } from './ExportButton'

// 「下載遞狀包」（設計 5.11）：只有律師能下載；阻擋條件與匯出 Word 相同
export default function FilingPackageButton({ draftId, blockers, canReview }: {
  draftId: string; blockers: api.Blocker[]; canReview: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [found, setFound] = useState<api.Blocker[]>([])  // 下載時後端重新檢查擋下的問題
  const [error, setError] = useState<string | null>(null)

  async function download() {
    setBusy(true)
    setError(null)
    setFound([])
    try {
      const { blob, filename } = await api.downloadFilingPackage(draftId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)  // 立刻釋放可能讓部分瀏覽器中斷下載
    } catch (e) {
      if (e instanceof api.ExportBlockedError) setFound(e.blockers)
      else setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1">
      <button type="button" onClick={download} disabled={busy || blockers.length > 0 || !canReview}
        className={`${BTN_SECONDARY} disabled:cursor-not-allowed`}>
        下載遞狀包
      </button>
      {!canReview && <p className="text-xs text-slate-500">只有律師能下載遞狀包</p>}
      <p className="text-xs text-slate-500">內含書狀 Word、證物清單、引用到的證物原檔；線上遞狀前請另存 PDF 並簽章。</p>
      {found.length > 0 && (
        <ul className="space-y-0.5 text-sm text-red-700">
          {found.map((b, i) => <li key={i}>{blockerPlace(b)}：{reasonLabel(b.reason)}</li>)}
        </ul>
      )}
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

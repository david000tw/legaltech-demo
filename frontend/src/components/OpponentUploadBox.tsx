import { useRef, useState } from 'react'
import * as api from '../api'
import { KIND_LABEL, UPLOAD_ACCEPT } from '../labels'

// 「書狀」分頁頂端的上傳框（設計 6.1、6.2、6.4）：不用先按「建立新一輪」，上傳就自動放進還沒起草的最新一輪或新的一輪。
// 自動化程度是整個案件的設定，從下一份上傳開始生效；答辯策略存在這次上傳的那一輪。
const MODES: [api.AutomationMode, string][] = [
  ['auto', '全自動寫到初稿'], ['pause', '拆完主張先停，讓我勾選'], ['manual', '每一步我自己按'],
]

export default function OpponentUploadBox({ caseId, ourSide, automation, target, onUploaded, onError }: {
  caseId: string
  ourSide: 'plaintiff' | 'defendant'
  automation: api.AutomationMode
  target: api.OpponentTarget
  onUploaded: () => Promise<void>
  onError: (message: string | null) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<api.BriefKind>(target.brief_kind)
  const [strategy, setStrategy] = useState('')
  const [busy, setBusy] = useState(false)
  const kinds: api.BriefKind[] = ourSide === 'plaintiff' ? ['preparatory'] : ['answer', 'preparatory']

  async function setMode(mode: api.AutomationMode) {
    onError(null)
    try {
      await api.patchCase(caseId, { automation: mode })
      await onUploaded()
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function upload() {
    const files = Array.from(fileRef.current?.files ?? [])
    if (files.length === 0) return
    onError(null)
    setBusy(true)
    try {
      await api.uploadDocuments(caseId, files, 'opponent_brief', undefined, undefined,
        { briefKind: target.new ? kind : undefined, strategy })
      setStrategy('')
    } catch (e) {
      onError((e as Error).message)
    } finally {
      if (fileRef.current) fileRef.current.value = ''
      setBusy(false)
      await onUploaded()  // 失敗也重讀：已上傳的那幾份要出現；排定的自動流程打開頁面時會補做
    }
  }

  return (
    <section aria-label="上傳對方的新書狀" className="space-y-2 rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <h3 className="font-semibold text-slate-900">上傳對方的新書狀</h3>
      <fieldset className="flex flex-wrap gap-x-4 gap-y-1">
        <legend className="mb-1 text-xs text-slate-600">上傳之後</legend>
        {MODES.map(([mode, label]) => (
          <label key={mode} className="inline-flex items-center gap-1">
            <input type="radio" name="automation" checked={automation === mode} onChange={() => void setMode(mode)} />
            {label}
          </label>
        ))}
      </fieldset>
      <p className="text-slate-700">
        {target.new ? `會建立新的一輪（第 ${target.seq} 輪）` : `會放進：第 ${target.seq} 輪`}
      </p>
      {target.new && (
        <label className="block text-xs text-slate-600">
          新一輪的書狀種類
          <select aria-label="新一輪的書狀種類" value={kind} onChange={(e) => setKind(e.target.value as api.BriefKind)}
            className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
            {kinds.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
        </label>
      )}
      <label className="block text-xs text-slate-600">
        答辯策略（選填）
        <textarea aria-label="答辯策略（選填）" value={strategy} onChange={(e) => setStrategy(e.target.value)} rows={2}
          maxLength={2000} placeholder="例如：主打原告與有過失"
          className="mt-1 w-full rounded border border-slate-300 p-2 text-sm" />
      </label>
      <label className="block text-xs text-slate-600">
        上傳對方的書狀
        <input ref={fileRef} type="file" accept={UPLOAD_ACCEPT} multiple disabled={busy} onChange={() => void upload()}
          aria-label="上傳對方的書狀" className="mt-1 block text-sm" />
      </label>
    </section>
  )
}

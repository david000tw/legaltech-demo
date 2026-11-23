import type { CitationView } from '../api'
import { reasonLabel } from './ExportButton'

type State = 'failed' | 'warn' | 'unconfirmed' | 'ok'

const STYLE: Record<State, string> = {
  failed: 'border-red-300 bg-red-50 text-red-800',
  warn: 'border-orange-400 bg-yellow-100 text-orange-900',
  unconfirmed: 'border-amber-300 bg-amber-50 text-amber-800',
  ok: 'border-emerald-300 bg-emerald-50 text-emerald-800',
}

export function citationState(c: CitationView): State {
  if (c.verify_status === 'warn' && !c.court_confirmed) return 'warn'
  if (c.verify_status !== 'ok' && c.verify_status !== 'warn') return 'failed'
  if (c.kind === 'judgment' && !c.lawyer_confirmed) return 'unconfirmed'
  return 'ok'
}

export default function CitationTag({ citation, onClick }: { citation: CitationView; onClick: () => void }) {
  const state = citationState(citation)
  const title = state === 'failed' ? citation.verify_reasons.map(reasonLabel).join('；')
    : state === 'warn' ? reasonLabel(`party_claim_unconfirmed:${citation.code}`)
      : state === 'unconfirmed' ? '判決引用：請讀原文後確認' : '已通過驗證'
  return (
    <button type="button" onClick={onClick} data-state={state} title={title}
      className={`rounded border px-2 py-0.5 text-xs ${STYLE[state]}`}>
      {citation.code}・{citation.label ?? '來源不明'}
    </button>
  )
}

import { ApiError, type Blocker } from '../api'
import { useState } from 'react'
import { BTN_PRIMARY } from '../ui'

// 驗證與審核原因代碼的中文說明（對應後端 app/verify/citations.py 與審核規則）。
export const REASON_LABELS: Record<string, (arg: string) => string> = {
  unreviewed: () => '段落尚未審核',
  malformed_marker: () => '引用標記格式錯誤，請改用系統提供的標記',
  unbound_reference: (a) => `內文直接寫了字號、條號或證物號「${a}」，請改用引用標記`,
  marker_without_citation: (a) => `引用標記 ${a} 沒有對應的引用資料`,
  citation_without_marker: (a) => `引用 ${a} 沒有出現在內文`,
  quote_marker_without_quote: (a) => `要求帶入引文，但 ${a} 沒有引文`,
  citation_quote_conflict: (a) => `同一引用的引文不一致：${a} 在同一段出現了兩段不同的引文，系統只保留第一段，請改寫或拆開`,
  quote_conflict: () => '同一代碼出現了不同的引文',
  reviewed_text_changed: () => '審核後顯示內容有變更（例如系統調整引用格式），已退回未審，請重新檢視後標記已審',
  inline_quote_unverified: (a) => `自行加引號的文字「${a}…」不是逐字出自對造書狀`,
  citation_failed: (a) => `引用 ${a} 驗證失敗`,
  judgment_unconfirmed: (a) => `${a}：打開原文後，按該段「已審」一併確認`,
  judgment_not_opened: (a) => `還要先看原文（${a}）`,
  unknown_code: () => '代碼不在本輪候選清單',
  quote_not_found: () => '引文不是逐字出自來源',
  quote_too_short: () => '引文少於 15 字',
  not_court_reasoning: () => '引用的不是法院判斷段',
  source_withdrawn: () => '判決已被撤下或更正',
  statute_missing: () => '查無此條文',
  possible_party_claim: () => '引文可能是法院轉述的當事人主張，請讀原文後確認',
  party_claim_unconfirmed: (a) => `引文可能是法院轉述的當事人主張，請讀原文後確認（${a}）`,
  quote_contains_case_number: () => '卷證引文夾帶判決字號，請勿整段引用',
  marker_in_heading: () => '標題不可放引用標記，請移到內文',
  control_char: () => '內容含有無法放進 Word 的控制字元',
  empty_draft: () => '草稿沒有任何段落',
  source_superseded: () => '卷證文字已更新，請重新確認這筆引用',
  numbered_cross_reference: (a) => `內文用段落編號參照其他段落「${a}」，刪段後編號會變，請改寫成該段的主題`,
  statement_blank: () => '聲明還有○○空格',
  statement_amount_missing: () => '金錢聲明缺少系統金額',
  statement_missing: () => '聲明是空的，起訴狀、答辯狀一定要有聲明',
  amounts_unchecked: () => '多位原告或非連帶被告：請律師核對金額',
  amount_unverified: (a) => `金額未對到請求項目或卷證：${Number(a).toLocaleString('en-US')}元`,
  amount_marker_misplaced: () => '系統金額標記只能用在起訴狀的訴之聲明',
  claim_value_missing: () => '請填寫訴訟標的金額或價額',
  // 計畫 2A：寫進書狀的辨識文字要先對照原圖
  image_unverified: (a) => `${a} 的頁尚未對照原圖`,
  quote_unreadable: () => '引文含看不清楚的字，請先修正辨識文字',
  inline_quote_image_unverified: (a) => `引文「${a}…」出自未對照原圖的對造書狀頁`,
  amount_image_unverified: (a) => {
    const i = a.indexOf(':')
    const n = i < 0 ? a : a.slice(0, i)
    return `金額 ${Number(n).toLocaleString('en-US')} 出自未對照原圖的辨識文字（${i < 0 ? '' : a.slice(i + 1)}）`
  },
  // 計畫 2B：支持度檢查
  support_pending: (a) => `${a} 支持度檢查中`,
  support_unchecked: (a) => `${a} 支持度尚未檢查，請重新檢查或填理由放行`,
  support_not_supported: (a) => `${a} 經 AI 判斷不支持這句話，請修改或填理由放行`,
  support_misuse: (a) => `${a} 經 AI 判斷是把當事人主張當成法院見解，請修改或填理由放行`,
  support_partial: (a) => `${a} 部分支持：看過說明後，按該段「已審」一併接受`,
}

// 阻擋原因的位置：聲明與整份書狀沒有段落編號
export function blockerPlace(b: Blocker): string {
  if (b.scope === 'draft') return '整份書狀'
  if (b.scope === 'statement') return '聲明'
  return `第 ${b.paragraph_no ?? b.paragraph_seq} 段`
}

export function reasonLabel(reason: string): string {
  const i = reason.indexOf(':')
  const code = i < 0 ? reason : reason.slice(0, i)
  const arg = i < 0 ? '' : reason.slice(i + 1)
  const f = REASON_LABELS[code]
  return f ? f(arg) : `其他問題（${reason}）`
}

// 操作失敗的畫面文字：被問題擋下（帶原因代碼）時把代碼翻成中文，其餘照後端訊息
export function errorMessage(e: unknown): string {
  const err = e as Error
  if (e instanceof ApiError && e.reasons?.length) return `${err.message}：${e.reasons.map(reasonLabel).join('；')}`
  return err.message
}

export default function ExportButton({ blockers, onExport }: {
  blockers: Blocker[]; onExport: () => void | Promise<void>
}) {
  const blocked = blockers.length > 0
  const [busy, setBusy] = useState(false)  // 匯出中不能再按：同時兩個匯出會搶同一份草稿的驗證寫入
  async function run() {
    setBusy(true)
    try {
      await onExport()
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2">
      <button type="button" onClick={run} disabled={blocked || busy}
        className={`${BTN_PRIMARY} disabled:cursor-not-allowed`}>
        {busy ? '匯出中…' : '匯出 Word'}
      </button>
      {blocked && (
        <ul className="space-y-0.5 text-sm text-red-700">
          {blockers.map((b, i) => <li key={i}>{blockerPlace(b)}：{reasonLabel(b.reason)}</li>)}
        </ul>
      )}
    </div>
  )
}

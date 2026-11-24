import type { Blocker } from '../api'

// 驗證與審核原因代碼的中文說明（對應後端 app/verify/citations.py 與審核規則）。
export const REASON_LABELS: Record<string, (arg: string) => string> = {
  unreviewed: () => '段落尚未審核',
  malformed_marker: () => '引用標記格式錯誤，請改用系統提供的標記',
  unbound_reference: (a) => `內文直接寫了字號、條號或證物號「${a}」，請改用引用標記`,
  marker_without_citation: (a) => `引用標記 ${a} 沒有對應的引用資料`,
  citation_without_marker: (a) => `引用 ${a} 沒有出現在內文`,
  quote_marker_without_quote: (a) => `要求帶入引文，但 ${a} 沒有引文`,
  inline_quote_unverified: (a) => `自行加引號的文字「${a}…」不是逐字出自對造書狀`,
  citation_failed: (a) => `引用 ${a} 驗證失敗`,
  judgment_unconfirmed: (a) => `判決引用尚未確認（${a}）`,
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
}

// 阻擋原因的位置：聲明與整份書狀沒有段落編號
function blockerPlace(b: Blocker): string {
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

export default function ExportButton({ blockers, onExport }: { blockers: Blocker[]; onExport: () => void }) {
  const blocked = blockers.length > 0
  return (
    <div className="space-y-2">
      <button type="button" onClick={onExport} disabled={blocked}
        className="rounded bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300">
        匯出 Word
      </button>
      {blocked && (
        <ul className="space-y-0.5 text-sm text-red-700">
          {blockers.map((b, i) => <li key={i}>{blockerPlace(b)}：{reasonLabel(b.reason)}</li>)}
        </ul>
      )}
    </div>
  )
}

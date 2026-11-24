// 後端 API 用戶端。所有請求走同源 /api（開發時由 Vite 轉發到 127.0.0.1:8000），登入狀態存在 httponly cookie。

export class ApiError extends Error {
  status: number
  code?: string  // 後端以 {code, message} 回報的規則錯誤，例如 confirm_reset
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

// paragraph_no：畫面與匯出上的段落編號（刪段後會重排）；paragraph_seq 是存檔順序
// scope：paragraph＝內文段落；statement＝聲明區塊；draft＝整份書狀（後兩者沒有段落編號）
export type Blocker = { paragraph_seq: number; paragraph_no?: number; reason: string
  scope?: 'paragraph' | 'statement' | 'draft' }

export class ExportBlockedError extends Error {
  blockers: Blocker[]
  constructor(blockers: Blocker[]) {
    super('尚有問題未解決，不能匯出')
    this.blockers = blockers
  }
}

export type Me = { id: string; firm_id: string; email: string; display_name: string; role: string;
  bar_number: string | null; bar_verified: boolean }
export type CaseSummary = { id: string; title: string; our_side: 'plaintiff' | 'defendant'; case_type: string }
export type DocPage = { id: string; page_no: number; needs_ocr: boolean }
// filed_by_party_id：對造書狀的遞狀人（當事人被刪掉後變 null）
export type CaseDocument = { id: string; kind: 'evidence' | 'opponent_brief'; filename: string; round_id: string | null;
  filed_by_party_id: string | null; page_count: number; pages: DocPage[] }
export type Exhibit = { id: string; label: string; prefix: string; number: number; document_id: string;
  filename: string | null }
export type RoundSummary = { id: string; seq: number; status: RoundStatus; error: string | null
  brief_kind?: BriefKind; failed_stage?: 'extract' | 'draft' | null; copied_from_seq?: number | null }
// 狀頭欄位（設計 5.2）：都可以空著，匯出時留空白讓律師補；reference_date 是 YYYY-MM-DD
export type CaseHeader = { court: string | null; case_number: string | null; division: string | null; cause: string | null
  addressee: string | null; attorneys: string[]; reference_date: string | null }
// 歸屬：我方、對造、立場與我方相同（AI 不得反駁立場相同者）
export type Alignment = 'ours' | 'opposing' | 'aligned'
export type Party = { id: string; seq: number; name: string; role: string; alignment: Alignment; legal_rep: string | null
  position_note: string | null }
export type PartyInput = { id?: string; name: string; role: string; alignment: Alignment; legal_rep: string | null
  position_note: string | null }
export type Procedure = 'civil' | 'family'
export type CaseTypeOption = { code: string; label: string; procedure: Procedure; reference_date_label: string }
export type CaseOptions = { case_types: CaseTypeOption[]; party_roles: string[]
  alignments: { code: Alignment; label: string }[] }
// procedure 依案號字別判斷（例：家訴＝家事）；與案型預設不同時 procedure_warning 有內容
export type CaseDetail = CaseSummary & { header: CaseHeader; parties: Party[]; procedure: Procedure; brief_title: string
  addressee_default: string; procedure_warning: string | null; reference_date_label: string
  documents: CaseDocument[]; exhibits: Exhibit[]; rounds: RoundSummary[]
  // 建立輪次時可選的書狀種類（原告不能寫答辯狀，被告不能寫起訴狀）
  brief_kind_options?: { code: BriefKind; label: string }[]; default_brief_kind?: BriefKind }
// 建立／修改案件：狀頭欄位傳 null 代表清空；attorneys 建立時不傳＝預設帶入建立者（律師）
export type CaseInput = Partial<Omit<CaseHeader, 'attorneys'>> & { title: string; our_side: 'plaintiff' | 'defendant'
  case_type: string; attorneys?: string[] }
export type RoundStatus = 'created' | 'extracting' | 'extracted' | 'drafting' | 'drafted' | 'failed'
// AI 想引用、但沒用候選代碼填的項目（設計 5.7）：mapped＝系統對回；ambiguous_passage＝對到判決但段落不確定；unmatched＝不在候選清單
export type ReferenceView = { text: string; status: 'mapped' | 'ambiguous_passage' | 'unmatched'; code: string | null }
// 起訴狀的請求項目（kind＝request）另有金額、確認狀態與佐證原文；evidence.found＝原文在那一頁找得到
export type RequestEvidence = { exhibit: string; page_no: number | null; quote: string; found: boolean }
export type ClaimView = { id: string; seq: number; kind: 'opponent_claim' | 'request'; selected: boolean
  summary: string; quote: string; quote_verified: boolean; page_no: number | null; references?: ReferenceView[]
  request_type?: 'monetary' | 'non_monetary'; amount?: number | null
  amount_note?: 'amount_not_in_evidence' | 'no_receipt' | 'amount_missing' | null; basis_text?: string | null
  confirmed?: boolean; evidence?: RequestEvidence[] }
export type BriefKind = 'complaint' | 'answer' | 'preparatory'
export type RoundDetail = { id: string; seq: number; status: RoundStatus; error: string | null; stale: boolean;
  brief_kind: BriefKind; failed_stage: 'extract' | 'draft' | null; draft_id: string | null; claims: ClaimView[]
  can_review?: boolean; narrative?: string | null; joint_liability?: boolean; provisional_execution?: boolean
  claim_value?: number | null; statement_variant?: 'alive' | 'deceased' | null; total_amount?: number
  statement_preview?: string | null; pending_ocr_pages?: string[] }
export type RoundSettings = { narrative?: string | null; joint_liability?: boolean; provisional_execution?: boolean
  claim_value?: number | null; statement_variant?: 'alive' | 'deceased' | null }
export type CitationView = { id: string; code: string; kind: 'evidence' | 'judgment' | 'statute'; quote: string;
  label: string | null; verify_status: 'ok' | 'warn' | 'failed'; verify_reasons: string[]; lawyer_confirmed: boolean
  court_confirmed: boolean; annotation?: string | null }  // annotation：判決的註記，例如「少數見解：否定配偶權」
// display_heading＝系統編號＋標題（顯示與匯出一致）；heading_text＝去掉編號的標題（修改表單用）
export type ParagraphView = { id: string; seq: number; kind?: 'body' | 'statement'; heading: string
  display_heading: string; heading_text: string;
  text: string; rendered_text: string;
  version: string; status: 'unreviewed' | 'reviewed'; verify_reasons: string[]; reviewed_bar_number: string | null;
  reviewed_at: string | null; citations: CitationView[] }
// brief_title：依程序的書狀名稱（民事／家事準備書狀）；procedure_warning：案號與案型程序不一致時的提醒（不阻擋匯出）
export type DraftView = { id: string; round_id: string; case_id: string; can_review: boolean; brief_title: string
  procedure_warning: string | null; paragraphs: ParagraphView[]; blockers: Blocker[]
  // 起訴狀、答辯狀的聲明區塊（放在內文前、不佔段落編號）；body_label＝內文標題（事實及理由／答辯理由）
  brief_kind?: BriefKind; case_type?: string; statement?: ParagraphView | null; statement_label?: string | null
  body_label?: string | null }
export type CitationSource = { kind: string; title: string; text: string; span: [number, number] | null
  annotation?: string | null }
export type CitationInput = { code: string; quote: string }

async function detail(r: Response): Promise<{ message: string; code?: string }> {
  try {
    const body = await r.json()
    if (typeof body.detail === 'string') return { message: body.detail }
    if (Array.isArray(body.detail)) {  // 欄位驗證錯誤：只留說明文字
      return { message: `輸入資料有誤：${body.detail.map((e: { msg?: string }) => e.msg ?? '').filter(Boolean).join('；')}` }
    }
    if (body.detail && typeof body.detail.message === 'string') return { message: body.detail.message, code: body.detail.code }
    return { message: JSON.stringify(body.detail ?? body) }
  } catch {
    return { message: r.statusText }
  }
}

async function apiError(r: Response): Promise<ApiError> {
  const d = await detail(r)
  return new ApiError(r.status, d.message, d.code)
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined
  const r = await fetch(`/api${path}`, { credentials: 'same-origin', headers, ...init })
  if (!r.ok) throw await apiError(r)
  return r.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })

export const login = (email: string, password: string) => req<{ id: string }>('/auth/login', json('POST', { email, password }))
export const logout = () => req<{ ok: boolean }>('/auth/logout', json('POST'))
export const me = () => req<Me>('/me')
export const listCases = () => req<CaseSummary[]>('/cases')
export const getCaseOptions = () => req<CaseOptions>('/case-options')
export const createCase = (body: CaseInput) => req<CaseSummary>('/cases', json('POST', body))
export const patchCase = (id: string, body: Partial<CaseInput>) => req<CaseSummary>(`/cases/${id}`, json('PATCH', body))
// 依序整批替換：帶 id 的保留原列，沒列到的刪除（其遞狀人變空白）
export const putParties = (id: string, parties: PartyInput[]) => req<Party[]>(`/cases/${id}/parties`, json('PUT', parties))
export const getCase = (id: string) => req<CaseDetail>(`/cases/${id}`)
// 改對造書狀的遞狀人（null＝未指定）；下次執行起草才會用到
export const setDocumentFiler = (id: string, filedByPartyId: string | null) =>
  req<{ id: string; filed_by_party_id: string | null }>(`/documents/${id}`,
    json('PATCH', { filed_by_party_id: filedByPartyId }))
export const createRound = (caseId: string, briefKind?: BriefKind) =>
  req<{ id: string; seq: number }>(`/cases/${caseId}/rounds`, json('POST', briefKind ? { brief_kind: briefKind } : {}))
export const patchRound = (roundId: string, body: RoundSettings) =>
  req<Record<string, unknown>>(`/rounds/${roundId}`, json('PATCH', body))
// 以這一輪為基礎重新起草：開新的一輪，舊的一輪不動
export const copyRound = (roundId: string) => req<{ id: string; seq: number }>(`/rounds/${roundId}/copy`, json('POST'))
// 多位原告或非連帶被告時，律師勾選「金額已自行核對」
export const ackAmounts = (roundId: string) =>
  req<Record<string, unknown>>(`/rounds/${roundId}/amounts-checked`, json('POST'))

export function uploadDocument(caseId: string, file: File, kind: 'evidence' | 'opponent_brief', roundId?: string,
  filedByPartyId?: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  if (roundId) form.append('round_id', roundId)
  if (filedByPartyId) form.append('filed_by_party_id', filedByPartyId)
  return req<{ id: string; page_count: number }>(`/cases/${caseId}/documents`, { method: 'POST', body: form })
}

// 兩段式執行：先拆解（停在待確認，律師勾選），再起草
export const extractRound = (roundId: string) => req<{ status: string }>(`/rounds/${roundId}/extract`, json('POST'))
export const draftRound = (roundId: string) => req<{ status: string }>(`/rounds/${roundId}/draft`, json('POST'))
// 重新拆解：律師改過勾選時，後端回 409 confirm_reset，畫面要先確認再帶 confirm=true
export const resetRound = (roundId: string, confirm: boolean) =>
  req<{ status: string }>(`/rounds/${roundId}/reset`, json('POST', { confirm }))
export type ClaimPatch = { selected?: boolean; label?: string; request_type?: 'monetary' | 'non_monetary'
  amount?: number | null; basis_text?: string | null }
export const patchClaim = (claimId: string, body: ClaimPatch) => req<ClaimView>(`/claims/${claimId}`, json('PATCH', body))
// 起訴狀請求項目：只有律師能確認金額；可以手動新增、刪除
export const confirmClaim = (claimId: string) => req<ClaimView>(`/claims/${claimId}/confirm`, json('POST'))
export const addRequest = (roundId: string, body: { label: string; request_type: 'monetary' | 'non_monetary'
  amount: number | null }) => req<ClaimView>(`/rounds/${roundId}/requests`, json('POST', body))
export const deleteClaim = (claimId: string) =>
  req<{ id: string; deleted: boolean }>(`/claims/${claimId}`, { method: 'DELETE' })
export const getRound = (roundId: string) => req<RoundDetail>(`/rounds/${roundId}`)
export const getDraft = (draftId: string) => req<DraftView>(`/drafts/${draftId}`)
export const getCitationSource = (citationId: string) => req<CitationSource>(`/citations/${citationId}/source`)
export const patchParagraph = (id: string, text: string, citations: CitationInput[], heading: string) =>
  req<ParagraphView>(`/paragraphs/${id}`, json('PATCH', { text, citations, heading }))
export const rewriteParagraph = (id: string, instruction: string) =>
  req<{ id: string; status: string; text: string }>(`/paragraphs/${id}/rewrite`, json('POST', { instruction }))
export const confirmCitation = (id: string) => req<CitationView>(`/citations/${id}/confirm`, json('POST'))
// 律師核對過對不上請求項目或卷證的金額後放行
export const confirmAmount = (paragraphId: string, amount: number) =>
  req<ParagraphView>(`/paragraphs/${paragraphId}/confirm-amount`, json('POST', { amount }))
// 「可能是法院轉述的當事人主張」的引用：律師讀原文後確認此句為法院判斷
export const confirmCourt = (id: string) => req<CitationView>(`/citations/${id}/confirm-court`, json('POST'))
// version：律師畫面上那一版的指紋；後端發現內容已變就回 409，不會把沒看過的版本記成已審
export const reviewParagraph = (id: string, version: string) =>
  req<ParagraphView>(`/paragraphs/${id}/review`, json('POST', { version }))
export const renumberExhibit = (id: string, number: number) =>
  req<{ id: string; label: string; number: number }>(`/exhibits/${id}`, json('PATCH', { number }))

export async function exportDraft(draftId: string): Promise<Blob> {
  const r = await fetch(`/api/drafts/${draftId}/export`, { method: 'POST', credentials: 'same-origin' })
  if (r.status === 409) throw new ExportBlockedError((await r.json()).blockers)
  if (!r.ok) throw await apiError(r)
  return r.blob()
}

// 後端 API 用戶端。所有請求走同源 /api（開發時由 Vite 轉發到 127.0.0.1:8000），登入狀態存在 httponly cookie。

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export type Blocker = { paragraph_seq: number; reason: string }

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
export type CaseDocument = { id: string; kind: 'evidence' | 'opponent_brief'; filename: string; round_id: string | null;
  page_count: number; pages: DocPage[] }
export type Exhibit = { id: string; label: string; prefix: string; number: number; document_id: string;
  filename: string | null }
export type RoundSummary = { id: string; seq: number; status: RoundStatus; error: string | null }
export type CaseDetail = CaseSummary & { documents: CaseDocument[]; exhibits: Exhibit[]; rounds: RoundSummary[] }
export type RoundStatus = 'created' | 'running' | 'drafted' | 'failed'
// AI 想引用、但沒用候選代碼填的項目（設計 5.7）：mapped＝系統對回；ambiguous_passage＝對到判決但段落不確定；unmatched＝不在候選清單
export type ReferenceView = { text: string; status: 'mapped' | 'ambiguous_passage' | 'unmatched'; code: string | null }
export type ClaimView = { seq: number; summary: string; quote: string; quote_verified: boolean; page_no: number | null
  references?: ReferenceView[] }
export type RoundDetail = { id: string; seq: number; status: RoundStatus; error: string | null; stale: boolean;
  draft_id: string | null; claims: ClaimView[] }
export type CitationView = { id: string; code: string; kind: 'evidence' | 'judgment' | 'statute'; quote: string;
  label: string | null; verify_status: 'ok' | 'warn' | 'failed'; verify_reasons: string[]; lawyer_confirmed: boolean
  court_confirmed: boolean }
export type ParagraphView = { id: string; seq: number; heading: string; text: string; rendered_text: string;
  version: string; status: 'unreviewed' | 'reviewed'; verify_reasons: string[]; reviewed_bar_number: string | null;
  reviewed_at: string | null; citations: CitationView[] }
export type DraftView = { id: string; round_id: string; case_id: string; can_review: boolean;
  paragraphs: ParagraphView[]; blockers: Blocker[] }
export type CitationSource = { kind: string; title: string; text: string; span: [number, number] | null }
export type CitationInput = { code: string; quote: string }

async function detail(r: Response): Promise<string> {
  try {
    const body = await r.json()
    return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
  } catch {
    return r.statusText
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined
  const r = await fetch(`/api${path}`, { credentials: 'same-origin', headers, ...init })
  if (!r.ok) throw new ApiError(r.status, await detail(r))
  return r.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })

export const login = (email: string, password: string) => req<{ id: string }>('/auth/login', json('POST', { email, password }))
export const logout = () => req<{ ok: boolean }>('/auth/logout', json('POST'))
export const me = () => req<Me>('/me')
export const listCases = () => req<CaseSummary[]>('/cases')
export const createCase = (title: string, our_side: 'plaintiff' | 'defendant') =>
  req<CaseSummary>('/cases', json('POST', { title, our_side }))
export const getCase = (id: string) => req<CaseDetail>(`/cases/${id}`)
export const createRound = (caseId: string) => req<{ id: string; seq: number }>(`/cases/${caseId}/rounds`, json('POST'))

export function uploadDocument(caseId: string, file: File, kind: 'evidence' | 'opponent_brief', roundId?: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  if (roundId) form.append('round_id', roundId)
  return req<{ id: string; page_count: number }>(`/cases/${caseId}/documents`, { method: 'POST', body: form })
}

export const runRound = (roundId: string) => req<{ status: string }>(`/rounds/${roundId}/run`, json('POST'))
export const getRound = (roundId: string) => req<RoundDetail>(`/rounds/${roundId}`)
export const getDraft = (draftId: string) => req<DraftView>(`/drafts/${draftId}`)
export const getCitationSource = (citationId: string) => req<CitationSource>(`/citations/${citationId}/source`)
export const patchParagraph = (id: string, text: string, citations: CitationInput[], heading: string) =>
  req<ParagraphView>(`/paragraphs/${id}`, json('PATCH', { text, citations, heading }))
export const rewriteParagraph = (id: string, instruction: string) =>
  req<{ id: string; status: string; text: string }>(`/paragraphs/${id}/rewrite`, json('POST', { instruction }))
export const confirmCitation = (id: string) => req<CitationView>(`/citations/${id}/confirm`, json('POST'))
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
  if (!r.ok) throw new ApiError(r.status, await detail(r))
  return r.blob()
}

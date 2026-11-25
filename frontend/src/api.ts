// 後端 API 用戶端。所有請求走同源 /api（開發時由 Vite 轉發到 127.0.0.1:8000），登入狀態存在 httponly cookie。

export class ApiError extends Error {
  status: number
  code?: string  // 後端以 {code, message} 回報的規則錯誤，例如 confirm_reset
  reasons?: string[]  // 被擋下的原因代碼（review_blocked）；畫面用 reasonLabel 翻成中文
  constructor(status: number, message: string, code?: string, reasons?: string[]) {
    super(message)
    this.status = status
    this.code = code
    this.reasons = reasons
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
// 頁的辨識狀態（計畫 2A）：none＝檔案本身有文字；pending＝待辨識；read＝AI 已讀、校對中；done＝已辨識；failed＝辨識失敗
export type OcrState = 'none' | 'pending' | 'read' | 'done' | 'failed'
// needs_check＝目前文字是辨識或手動輸入、還沒對照原圖；verified＝已對照原圖（後端一律有這些欄位，舊測試資料可省略）
export type DocPage = { id: string; page_no: number; needs_ocr: boolean; ocr_state?: OcrState; needs_check?: boolean
  alert_count?: number; verified?: boolean }
export type OcrProgress = { total: number; done: number; failed: number; proofing: number; minutes_left: number }
// 辨識工作掛在文件上；waiting＝頁數超過上限，等律師確認後才開始
export type OcrJob = Omit<JobView, 'status'> & { status: 'running' | 'done' | 'failed' | 'waiting'
  document_id: string | null }
export type DocOcr = { job: OcrJob | null; progress: OcrProgress; can_reocr: boolean; scan_hint: boolean
  can_resume: boolean }
// filed_by_party_id：對造書狀的遞狀人（當事人被刪掉後變 null）
export type CaseDocument = { id: string; kind: 'evidence' | 'opponent_brief'; filename: string; round_id: string | null;
  filed_by_party_id: string | null; page_count: number; pages: DocPage[]; ocr?: DocOcr }
// 對照原圖面板（計畫 2A）：一則提醒＝文字上的 [start, end) 與校對員讀到的字、圖上的框（box：x1, y1, x2, y2）
export type OcrAlert = { kind: string; start: number; end: number; message: string; proof_text: string | null
  box: number[] | null; color: 'red' | 'yellow' }
export type PageOcr = { page_id: string; document_id: string; label: string | null; ocr_state: OcrState
  ocr_error: string | null; text: string | null; version: number | null; source: string | null; needs_check: boolean
  verified: { by_name: string | null; at: string | null } | null; alerts: OcrAlert[]; has_image: boolean
  can_edit: boolean; can_verify: boolean; can_save_verify: boolean; blank_unconfirmed: boolean }
export type UploadResult = { id: string; page_count: number
  ocr?: { status: OcrJob['status']; pages: number; minutes: number } | null }
export type Exhibit = { id: string; label: string; prefix: string; number: number; document_id: string;
  filename: string | null }
export type RoundSummary = { id: string; seq: number; status: RoundStatus; error: string | null
  brief_kind?: BriefKind; failed_stage?: 'extract' | 'draft' | null; copied_from_seq?: number | null
  has_health?: boolean }  // 有健檢結果（起草失敗時也看得到，設計 6.3）
// 狀頭欄位（設計 5.2）：都可以空著，匯出時留空白讓律師補；reference_date 是 YYYY-MM-DD
export type CaseHeader = { court: string | null; case_number: string | null; division: string | null; cause: string | null
  addressee: string | null; attorneys: string[]; reference_date: string | null }
// 歸屬：我方、對造、立場與我方相同（AI 不得反駁立場相同者）
export type Alignment = 'ours' | 'opposing' | 'aligned'
export type Party = { id: string; seq: number; name: string; role: string; alignment: Alignment; legal_rep: string | null
  position_note: string | null
  auto?: boolean; auto_verified?: boolean }  // 從對方書狀自動加入的對造、是否已核對（設計 6.6）
export type HeaderAutoField = { document_id: string | null; filename: string | null; page_no: number | null; verified: boolean }
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
  brief_kind_options?: { code: BriefKind; label: string }[]; default_brief_kind?: BriefKind
  // 卷證說明、大事記的 AI 工作狀態（沒執行過是 null）
  jobs?: { evidence_notes: JobView | null; timeline: JobView | null }
  // 沒有進行中辨識工作的待辨識頁數（「開始辨識（N 頁）」）
  pending_ocr_pages?: number
  automation?: AutomationMode  // 案件的自動化程度（設計 6.1）
  opponent_target?: OpponentTarget  // 上傳對方書狀會放進哪一輪（設計 6.2）
  header_auto?: Record<string, HeaderAutoField>  // 從對方書狀自動帶入的狀頭欄位（設計 6.6）
  header_unverified?: boolean }
export type AutomationMode = 'auto' | 'pause' | 'manual'
export type OpponentTarget = { round_id: string | null; seq: number; new: boolean; brief_kind: BriefKind }
// 新步驟（卷證說明、大事記、健檢）的原文出處（設計 5.1）：label 讀取時才算（證物可以改號）
// status：found＝摘錄在那一頁；not_found＝沒過（reasons 說明哪一項）；absence＝缺漏類；unchecked＝律師填的，不比對
export type SourceView = { page_id: string | null; label: string | null; quote: string
  status: 'found' | 'not_found' | 'absence' | 'unchecked'; reasons: string[]
  ocr_unverified?: boolean }  // 出處是還沒對照原圖的辨識文字（計畫 2A）
// 按鈕觸發的 AI 工作；stale＝心跳逾時，可以重新執行
export type JobView = { id: string; status: 'running' | 'done' | 'failed'; error: string | null; started_at: string
  stale: boolean }
export type EvidenceNoteView = { id: string; kind: 'proves' | 'caution'; text: string; origin: 'ai' | 'lawyer'
  source: SourceView }
// needs_notes＝從沒產生過或原文變了；pending_ocr＝掃描頁（待辨識，未送 AI）
export type EvidenceDocView = { document_id: string; exhibit: string; filename: string; needs_notes: boolean
  pending_ocr: string[]; notes: EvidenceNoteView[] }
// date：2025-03-09 或只到月 2025-03；date_label：民國年；source_kind＝opponent 的事件標「對造主張」
export type TimelineEventView = { id: string; date: string | null; date_label: string | null; description: string
  amount: number | null; source_kind: 'evidence' | 'opponent' | null; origin: 'ai' | 'lawyer'; source: SourceView | null }
// 書狀健檢（設計 5.8）：level high＝高風險、suggest＝補強建議、gap＝證據缺口；absence＝缺漏類（缺少某證據）
// references：本輪候選判決法條（label 讀取時才算）；not_in_list：AI 想引用但不在候選清單的
export type HealthItemView = { level: 'high' | 'suggest' | 'gap'; title: string; explanation: string
  claim_seqs: number[]; absence: boolean; basis: SourceView[]; references: { code: string; label: string }[]
  not_in_list: string[] }
// undisputed：兩造不爭執事項（只來自本輪對造書狀；起訴狀是 null）；outdated＝卷證、對造書狀或主張已變更
export type HealthReportView = { created_at: string; outdated: boolean; items: HealthItemView[]
  undisputed: { fact: string; basis: SourceView }[] | null; issues: string[]; issues_label: string
  pending_ocr: string[] }
export type TimelineEventInput = { date?: string | null; description?: string; amount?: number | null
  page_id?: string | null; quote?: string }
// 建立／修改案件：狀頭欄位傳 null 代表清空；attorneys 建立時不傳＝預設帶入建立者（律師）
export type CaseInput = Partial<Omit<CaseHeader, 'attorneys'>> & { title: string; our_side: 'plaintiff' | 'defendant'
  case_type: string; attorneys?: string[] }
export type RoundStatus = 'created' | 'extracting' | 'extracted' | 'drafting' | 'drafted' | 'failed'
// AI 想引用、但沒用候選代碼填的項目（設計 5.7）：mapped＝系統對回；ambiguous_passage＝對到判決但段落不確定；unmatched＝不在候選清單
export type ReferenceView = { text: string; status: 'mapped' | 'ambiguous_passage' | 'unmatched'; code: string | null }
// 起訴狀的請求項目（kind＝request）另有金額、確認狀態與佐證原文；evidence.found＝原文在那一頁找得到
export type RequestEvidence = { exhibit: string; page_no: number | null; quote: string; found: boolean
  page_id?: string | null; ocr_unverified?: boolean }
export type ClaimView = { id: string; seq: number; kind: 'opponent_claim' | 'request'; selected: boolean
  summary: string; quote: string; quote_verified: boolean; page_no: number | null; references?: ReferenceView[]
  request_type?: 'monetary' | 'non_monetary'; amount?: number | null
  amount_note?: 'amount_not_in_evidence' | 'no_receipt' | 'amount_missing' | null; basis_text?: string | null
  confirmed?: boolean; evidence?: RequestEvidence[]
  page_id?: string | null; ocr_unverified?: boolean }  // 出處頁；ocr_unverified＝辨識文字、還沒對照原圖
export type BriefKind = 'complaint' | 'answer' | 'preparatory'
export type RoundDetail = { id: string; seq: number; status: RoundStatus; error: string | null; stale: boolean;
  brief_kind: BriefKind; failed_stage: 'extract' | 'draft' | null; draft_id: string | null; claims: ClaimView[]
  can_review?: boolean; narrative?: string | null; joint_liability?: boolean; provisional_execution?: boolean
  claim_value?: number | null; statement_variant?: 'alive' | 'deceased' | null; total_amount?: number
  statement_preview?: string | null; pending_ocr_pages?: string[]
  health_job?: JobView | null  // 書狀健檢的 AI 工作（設計 5.8）
  extract_blocked?: string | null  // 對造書狀還沒辨識完等：「拆解」停用並顯示這段話
  strategy?: string | null  // 答辯策略（設計 6.4）
  automation?: AutomationView }  // 自動跑到哪、在等什麼（設計 6.2）
export type AutomationWait = { code: 'opponent_ocr' | 'evidence_ocr' | 'evidence_notes_running' | 'new_brief' | 'blocked'
  message: string; document_id: string | null; page_id: string | null
  hint: 'confirm_ocr' | 'resume_ocr' | 'retry_failed' | 'start_notes' | null }
export type AutomationView = { mode: AutomationMode; auto_next: 'extract' | 'draft' | null
  running: 'extract' | 'draft' | null; wait: AutomationWait | null; failed_stage: string | null }
export type RoundSettings = { narrative?: string | null; joint_liability?: boolean; provisional_execution?: boolean
  claim_value?: number | null; statement_variant?: 'alive' | 'deceased' | null; strategy?: string | null }
export type CitationView = { id: string; code: string; kind: 'evidence' | 'judgment' | 'statute'; quote: string;
  label: string | null; verify_status: 'ok' | 'warn' | 'failed'; verify_reasons: string[]; lawyer_confirmed: boolean
  opened?: boolean  // 判決引用：目前登入的律師在本草稿打開過被引用的那一段（設計 5.1）
  court_confirmed: boolean; annotation?: string | null  // annotation：判決的註記，例如「少數見解：否定配偶權」
  support?: SupportView | null }  // 支持度檢查（計畫 2B）；null＝這筆引用不需要檢查
export type SupportVerdict = 'supports' | 'partial' | 'not_supported' | 'party_claim_misuse'
export type SupportView = { status: 'pending' | 'done' | 'failed'; verdict: SupportVerdict | null
  explanation: string | null; error: string | null; blocking: boolean
  released: { name: string | null; bar_number: string | null; at: string; reason: string | null } | null
  version?: string | null }  // 這一版結果的代號：放行時帶回去，結果已變（例如別人按了重新檢查）後端回 409
// display_heading＝系統編號＋標題（顯示與匯出一致）；heading_text＝去掉編號的標題（修改表單用）
export type ParagraphView = { id: string; seq: number; kind?: 'body' | 'statement'; heading: string
  display_heading: string; heading_text: string;
  text: string; rendered_text: string;
  version: string; status: 'unreviewed' | 'reviewed'; verify_reasons: string[]; reviewed_bar_number: string | null;
  reviewed_at: string | null; citations: CitationView[]
  source_notice?: string | null  // 例：「依據頁已修正」
  review_requested?: boolean  // 「儲存並標記已審」後等支持度檢查完成（設計 5.2）
  review_note?: string | null }  // 自動完成審核被取消的原因
// brief_title：依程序的書狀名稱（民事／家事準備書狀）；procedure_warning：案號與案型程序不一致時的提醒（不阻擋匯出）
export type DraftView = { id: string; round_id: string; case_id: string; case_title: string; can_review: boolean
  brief_title: string
  procedure_warning: string | null; paragraphs: ParagraphView[]; blockers: Blocker[]
  // 起訴狀、答辯狀的聲明區塊（放在內文前、不佔段落編號）；body_label＝內文標題（事實及理由／答辯理由）
  brief_kind?: BriefKind; case_type?: string; statement?: ParagraphView | null; statement_label?: string | null
  body_label?: string | null
  // 起訴狀狀頭的訴訟標的金額（有金錢請求，預設＝請求項目加總）或價額（只有非金錢請求，要律師填）；其他書狀為 null
  claim_value?: { label: string; value: number | null } | null
  unverified_ocr_pages?: string[]  // 本輪資料來源中還沒對照原圖的辨識頁
  key_points?: KeyPoints }  // 這份草稿回應了對方哪幾點（設計 6.5）
export type KeyPointParagraph = { seq: number; no: number; heading: string }
export type KeyPoints = { legacy: boolean; complaint?: boolean
  items: { seq: number; summary: string; selected: boolean; uncovered: boolean; paragraphs: KeyPointParagraph[] }[] }
export type CitationSource = { kind: string; title: string; text: string; span: [number, number] | null
  annotation?: string | null
  // 卷證引用（計畫 2A）：原文卡片並排原圖；needs_check＝辨識文字、律師要對照後才能標記已審
  page_id?: string; has_image?: boolean; needs_check?: boolean; version?: number
  alerts?: OcrAlert[] }  // 這一頁目前版本的提醒（卡片勾「已對照原圖」會解除整頁，要看得到）
export type CitationInput = { code: string; quote: string }

async function detail(r: Response): Promise<{ message: string; code?: string; reasons?: string[] }> {
  try {
    const body = await r.json()
    if (typeof body.detail === 'string') return { message: body.detail }
    if (Array.isArray(body.detail)) {  // 欄位驗證錯誤：只留說明文字
      return { message: `輸入資料有誤：${body.detail.map((e: { msg?: string }) => e.msg ?? '').filter(Boolean).join('；')}` }
    }
    if (body.detail && typeof body.detail.message === 'string') {
      return { message: body.detail.message, code: body.detail.code,
        reasons: Array.isArray(body.detail.reasons) ? body.detail.reasons : undefined }
    }
    return { message: JSON.stringify(body.detail ?? body) }
  } catch {
    return { message: r.statusText }
  }
}

async function apiError(r: Response): Promise<ApiError> {
  const d = await detail(r)
  return new ApiError(r.status, d.message, d.code, d.reasons)
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
export const patchCase = (id: string, body: Partial<CaseInput> & { automation?: AutomationMode }) =>
  req<CaseSummary>(`/cases/${id}`, json('PATCH', body))
// 「狀頭核對無誤」：確認所有自動帶入、還沒核對的狀頭欄位與對造（限律師，設計 6.6）
export const verifyHeader = (caseId: string) =>
  req<{ header_unverified: boolean }>(`/cases/${caseId}/header-verify`, json('POST'))
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
  return req<UploadResult>(`/cases/${caseId}/documents`, { method: 'POST', body: form })
}

const PHOTO = /\.(jpe?g|png)$/i

function uploadOne(caseId: string, files: File[], kind: 'evidence' | 'opponent_brief', roundId?: string,
  filedByPartyId?: string, extra: Record<string, string> = {}) {
  const form = new FormData()
  const field = files.length === 1 && !PHOTO.test(files[0].name) ? 'file' : 'files'
  for (const f of files) form.append(field, f)
  form.append('kind', kind)
  if (roundId) form.append('round_id', roundId)
  if (filedByPartyId) form.append('filed_by_party_id', filedByPartyId)
  for (const [k, v] of Object.entries(extra)) form.append(k, v)
  return req<UploadResult>(`/cases/${caseId}/documents`, { method: 'POST', body: form })
}

// 對方書狀建新輪次時的書狀種類、答辯策略（設計 6.2、6.4）
export type UploadOptions = { briefKind?: BriefKind; strategy?: string }

// 上傳：照片合成一份（一個證物號）；PDF、Word、純文字逐份上傳，各自一個證物號（後端一次只收一份文件）。
// 依檔名排序（數字照大小：1、2、10），證物號才會照「01_、02_」的順序。中途失敗就停下，說明哪幾份沒上傳
const byName = (a: File, b: File) => a.name.localeCompare(b.name, 'zh-Hant', { numeric: true })

export async function uploadDocuments(caseId: string, files: File[], kind: 'evidence' | 'opponent_brief',
  roundId?: string, filedByPartyId?: string, opts: UploadOptions = {}): Promise<UploadResult[]> {
  const sorted = [...files].sort(byName)
  const photos = sorted.filter((f) => PHOTO.test(f.name))
  const batches: File[][] = []
  for (const f of sorted) {
    if (!PHOTO.test(f.name)) batches.push([f])
    else if (f === photos[0]) batches.push(photos)  // 照片在第一張照片的位置合成一份
  }
  const done: UploadResult[] = []
  for (const [i, batch] of batches.entries()) {
    try {
      // 最後一份才觸發自動流程（一次選多份只拆解一次，設計 6.2）；中途失敗的話，打開頁面時會補做
      const extra: Record<string, string> = { batch_last: i === batches.length - 1 ? 'true' : 'false' }
      if (opts.briefKind) extra.brief_kind = opts.briefKind
      if (opts.strategy?.trim()) extra.strategy = opts.strategy.trim()
      done.push(await uploadOne(caseId, batch, kind, roundId, filedByPartyId, extra))
    } catch (e) {
      if (batches.length === 1) throw e
      const left = batches.slice(i).flat().map((f) => `「${f.name}」`).join('')
      const head = done.length ? `已上傳 ${done.length} 份；` : ''
      throw new Error(`「${batch[0].name}」上傳失敗：${(e as Error).message}（${head}未上傳：${left}）`)
    }
  }
  return done
}

// 辨識（計畫 2A）：頁數超過上限時回 409 confirm_ocr（ApiError.code），畫面確認後帶 confirm 再送一次
type OcrStart = { job: OcrJob | null; progress: OcrProgress }
export const startDocumentOcr = (docId: string, opts: { confirm?: boolean; retryFailed?: boolean } = {}) =>
  req<OcrStart>(`/documents/${docId}/ocr`, json('POST', { confirm: !!opts.confirm, retry_failed: !!opts.retryFailed }))
export const reocrDocument = (docId: string, confirm: boolean) =>
  req<OcrStart & { pages: number }>(`/documents/${docId}/reocr`, json('POST', { confirm }))
// 辨識橫幅輪詢用的輕量狀態（只有橫幅要的欄位；狀態變了才重讀整個案件）
export type DocOcrStatus = Pick<DocOcr, 'job' | 'progress' | 'can_resume'>
export const getDocumentOcrStatus = (docId: string) => req<DocOcrStatus>(`/documents/${docId}/ocr-status`)
export const startCaseOcr = (caseId: string, confirm: boolean) =>
  req<{ jobs: OcrJob[]; pages: number }>(`/cases/${caseId}/ocr`, json('POST', { confirm }))
export const getPageOcr = (pageId: string) => req<PageOcr>(`/pages/${pageId}/ocr`)
// baseVersion：畫面上那一版（失敗頁沒有版本＝null）；版本不符回 409「這一頁的文字已更新，請重新載入」
export const savePageText = (pageId: string, text: string, baseVersion: number | null, verify: boolean) =>
  req<PageOcr>(`/pages/${pageId}/text`, json('PUT', { text, base_version: baseVersion, verify }))
export const verifyPage = (pageId: string, version: number) =>
  req<PageOcr>(`/pages/${pageId}/verify`, json('POST', { version }))
export const confirmBlank = (pageId: string) => req<PageOcr>(`/pages/${pageId}/blank-confirm`, json('POST'))
export const pageImageUrl = (pageId: string) => `/api/pages/${pageId}/image`

// 兩段式執行：先拆解（停在待確認，律師勾選），再起草
export const extractRound = (roundId: string) => req<{ status: string }>(`/rounds/${roundId}/extract`, json('POST'))
// force：「先起草（不含還沒辨識的頁）」；ignoreNewBriefs：「不納入，直接起草」（設計 6.2）
export const draftRound = (roundId: string, opts: { force?: boolean; ignoreNewBriefs?: boolean } = {}) =>
  req<{ status: string }>(`/rounds/${roundId}/draft`, json('POST', {
    ...(opts.force ? { force: true } : {}), ...(opts.ignoreNewBriefs ? { ignore_new_briefs: true } : {}) }))
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
// 支持度檢查（計畫 2B）：律師填理由放行；重新檢查＝整份草稿尚未檢查成功的引用合併重查
export const releaseSupport = (citationId: string, reason: string, version: string) =>
  req<ParagraphView>(`/citations/${citationId}/support/release`, json('POST', { reason, version }))
export const retrySupport = (draftId: string) =>
  req<{ queued: number }>(`/drafts/${draftId}/support/retry`, json('POST'))
// version：律師畫面上那一版的指紋；後端發現內容已變就回 409，不會把沒看過的版本記成已審
// defer：「儲存並標記已審」——只差支持度還在檢查時先記下，查完全部「支持」自動完成（設計 5.2）
export const reviewParagraph = (id: string, version: string, opts: { defer?: boolean } = {}) =>
  req<ParagraphView>(`/paragraphs/${id}/review`, json('POST', opts.defer ? { version, defer: true } : { version }))
export const renumberExhibit = (id: string, number: number) =>
  req<{ id: string; label: string; number: number }>(`/exhibits/${id}`, json('PATCH', { number }))

// 電子遞狀包（設計 5.11）：書狀、證物清單、引用到的證物原檔、說明打包成 zip；檔名取自回應標頭
export async function downloadFilingPackage(draftId: string): Promise<{ blob: Blob; filename: string }> {
  const r = await fetch(`/api/drafts/${draftId}/filing-package`, { method: 'POST', credentials: 'same-origin' })
  if (r.status === 409) throw new ExportBlockedError((await r.json()).blockers)
  if (!r.ok) throw await apiError(r)
  const m = /filename\*=UTF-8''([^;]+)/i.exec(r.headers.get('Content-Disposition') ?? '')
  let filename = '遞狀包.zip'
  if (m) {
    try {
      filename = decodeURIComponent(m[1])
    } catch {
      // 標頭編碼壞掉時用預設檔名
    }
  }
  return { blob: await r.blob(), filename }
}

export async function exportDraft(draftId: string): Promise<Blob> {
  const r = await fetch(`/api/drafts/${draftId}/export`, { method: 'POST', credentials: 'same-origin' })
  if (r.status === 409) throw new ExportBlockedError((await r.json()).blockers)
  if (!r.ok) throw await apiError(r)
  return r.blob()
}

// 卷證說明（設計 5.6）：按鈕觸發的 AI 工作，結果存起來；律師與助理都可以修改、刪除
export const generateEvidenceNotes = (caseId: string) =>
  req<{ job: JobView }>(`/cases/${caseId}/evidence-notes`, json('POST'))
export const getEvidenceNotes = (caseId: string) =>
  req<{ documents: EvidenceDocView[]; job: JobView | null }>(`/cases/${caseId}/evidence-notes`)
export const patchEvidenceNote = (id: string, text: string) =>
  req<EvidenceNoteView>(`/evidence-notes/${id}`, json('PATCH', { text }))
export const deleteEvidenceNote = (id: string) =>
  req<{ id: string; deleted: boolean }>(`/evidence-notes/${id}`, { method: 'DELETE' })
// 大事記（設計 5.7）：重新整理只取代 AI 的事件，律師新增或修改的保留
export const generateTimeline = (caseId: string) => req<{ job: JobView }>(`/cases/${caseId}/timeline`, json('POST'))
export const getTimeline = (caseId: string) =>
  req<{ events: TimelineEventView[]; pending_ocr: string[]; outdated?: boolean; job: JobView | null }>(
    `/cases/${caseId}/timeline`)  // outdated＝整理之後卷證有新增或變更
// 卷證裡有、大事記沒列的完整日期（純規則，按鈕才查）；date_label＝民國年；sources 最多 3 筆，source_count＝全部處數
export type UncoveredDate = { date: string; date_label: string; source_count: number
  sources: { label: string; page_no: number; snippet: string; page_id: string }[] }
export const getUncoveredDates = (caseId: string) =>
  req<{ total: number; dates: UncoveredDate[] }>(`/cases/${caseId}/timeline/uncovered-dates`)
export const addTimelineEvent = (caseId: string, body: TimelineEventInput) =>
  req<TimelineEventView>(`/cases/${caseId}/timeline/events`, json('POST', body))
export const patchTimelineEvent = (id: string, body: TimelineEventInput) =>
  req<TimelineEventView>(`/timeline-events/${id}`, json('PATCH', body))
export const deleteTimelineEvent = (id: string) =>
  req<{ id: string; deleted: boolean }>(`/timeline-events/${id}`, { method: 'DELETE' })
// 看原文：卷證說明、大事記、健檢的出處（形狀與引用的原文對照相同）
export const getPageSource = (pageId: string, quote: string) =>
  req<CitationSource>(`/pages/${pageId}/source?quote=${encodeURIComponent(quote)}`)
// 書狀健檢與爭點整理表（設計 5.8）：起草完成後才能執行；律師與助理都可以
export const runHealthCheck = (roundId: string) => req<{ job: JobView }>(`/rounds/${roundId}/health`, json('POST'))
export const getHealthReport = (roundId: string) =>
  req<{ report: HealthReportView | null; job: JobView | null }>(`/rounds/${roundId}/health`)

// 判決檢索與釘選（設計 5.9）：只列法院判斷段；pin_id 有值＝本案已釘選
export type PassageView = { passage_id: string; citation: string; court: string; decided_on: string; text: string
  annotation: string | null; pin_id: string | null; note: string }
export type ImportedJudgment = { judgment: { citation: string; court: string; decided_on: string }; created: boolean
  passages: PassageView[] }
export const searchJudgments = (caseId: string, q: string, scope: 'case_type' | 'all') =>
  req<{ results: PassageView[]; terms: string[] }>(
    `/cases/${caseId}/judgment-search?q=${encodeURIComponent(q)}&scope=${scope}`)
export const listPins = (caseId: string) => req<PassageView[]>(`/cases/${caseId}/pins`)
export const pinPassage = (caseId: string, passageId: string, note: string) =>
  req<PassageView>(`/cases/${caseId}/pins`, json('POST', { passage_id: passageId, note }))
export const patchPin = (pinId: string, note: string) => req<PassageView>(`/pins/${pinId}`, json('PATCH', { note }))
export async function deletePin(pinId: string): Promise<void> {
  const r = await fetch(`/api/pins/${pinId}`, { method: 'DELETE', credentials: 'same-origin' })
  if (!r.ok) throw await apiError(r)
}
// 貼司法院判決網址加入判決庫：伺服器自己向司法院下載
export const importJudgment = (caseId: string, url: string) =>
  req<ImportedJudgment>(`/cases/${caseId}/judgments/import`, json('POST', { url }))
// 把釘選的判決插到草稿段落結尾；引文必填（至少 15 字、取自判決原文）
export const insertPin = (paragraphId: string, pinId: string, quote: string) =>
  req<{ paragraph_id: string; code: string }>(`/paragraphs/${paragraphId}/insert-pin`,
    json('POST', { pin_id: pinId, quote }))

// 裁判費與期限試算（設計 5.10）：每個結果附逐步依據；notes 一定含「試算僅供參考，律師須自行核對」
export type ToolStep = { label: string; value: string; basis: string }
export type FeeInput = { property: boolean; amount: number; instance: 1 | 2 | 3; appeal_amount?: number }
export type FeeResult = { fee: number | null; steps: ToolStep[]; notes: string[] }
export type ServiceMethod = 'direct' | 'deposit' | 'public' | 'foreign_public' | 'continued_public'
export type ServiceInput = { who: 'party' | 'attorney'; served_on: string; method: ServiceMethod }
export type DeadlineProcedure = 'civil' | 'family_litigation' | 'family_nonlitigation'
export type DeadlineInput = { kind: 'appeal' | 'interlocutory' | 'custom'; procedure: DeadlineProcedure
  services: ServiceInput[]; travel_days: number; attorney_local: boolean; custom_days?: number }
export type DeadlineResult = { due: string; steps: ToolStep[]; notes: string[] }
export type ToolDefaults = { amount: number | null; amount_label: string; deadline_procedure: DeadlineProcedure
  copies: number | null; copies_steps: ToolStep[]; notes: string[] }
export const courtFee = (body: FeeInput) => req<FeeResult>('/tools/court-fee', json('POST', body))
export const deadline = (body: DeadlineInput) => req<DeadlineResult>('/tools/deadline', json('POST', body))
export const toolDefaults = (caseId: string) => req<ToolDefaults>(`/cases/${caseId}/tool-defaults`)

// 用量與費用（設計 5.12）：律師與事務所管理員可看、可改匯率；單價只有事務所管理員能改
export type UsageRow = { source: string; model: string; step: string; calls: number; failed: number; input: number
  output: number; cache_read: number; cache_write: number; usd: number | null; twd: number | null
  billing: 'metered' | 'unmetered' | null; priced: boolean }
export type Money = { usd: number; twd: number }
export type RateView = { usd_twd: number; rate_set_on: string; is_default: boolean }
export type UsageSummary = { rows: UsageRow[]; totals: { metered: Money; unmetered: Money }; notes: string[]
  rate: RateView; filters: { months: string[]; steps: string[]; sources: string[]; models: string[]
    cases: { id: string; title: string }[] } }
export type UsageFilters = { month?: string; case_id?: string; step?: string; source?: string; model?: string }
export type Price = { id: string; source: string; model: string; input_usd: number; output_usd: number
  cache_read_usd: number; cache_write_5m_usd: number; cache_write_1h_usd: number; billing: 'metered' | 'unmetered'
  note: string }
export type PriceValues = Omit<Price, 'id' | 'source' | 'model'>
export const getUsage = (filters: UsageFilters = {}) => {
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]).toString()
  return req<UsageSummary>(`/usage${qs ? `?${qs}` : ''}`)
}
export const getPrices = () => req<Price[]>('/usage/prices')
export const postPrice = (body: Omit<Price, 'id'>) => req<Price>('/usage/prices', json('POST', body))
export const putPrice = (id: string, body: PriceValues) => req<Price>(`/usage/prices/${id}`, json('PUT', body))
export const putRate = (body: { usd_twd: number; rate_set_on: string }) => req<RateView>('/usage/rate', json('PUT', body))

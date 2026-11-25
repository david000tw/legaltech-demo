import * as api from '../api'

// 案件資料分頁：從對方書狀自動帶入的狀頭欄位與對造（設計 6.6）。帶錯會直接印在書狀上，所以要律師核對後才能匯出。
const FIELD_LABEL: Record<string, string> = { court: '法院', case_number: '案號', division: '股別', cause: '案由' }

export default function HeaderAutoNotice({ caseId, auto, parties, missing = [], unverified, canVerify, onVerified,
  onError, onOpenDocument }: {
  caseId: string
  auto: Record<string, api.HeaderAutoField>
  parties: api.Party[]
  missing?: string[]  // 有對方書狀、但這些欄位讀不到也還空著（例如起訴狀送到被告手上時還沒有案號）
  unverified: boolean
  canVerify: boolean
  onVerified: () => Promise<void>
  onError: (message: string | null) => void
  onOpenDocument: (documentId: string) => void
}) {
  const fields = Object.entries(auto)
  const autoParties = parties.filter((p) => p.auto)
  if (fields.length === 0 && autoParties.length === 0 && missing.length === 0) return null

  async function verify() {
    onError(null)
    try {
      await api.verifyHeader(caseId)
      await onVerified()
    } catch (e) {
      onError((e as Error).message)
    }
  }

  return (
    <section aria-label="自動帶入的狀頭" className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
      <ul className="space-y-0.5 text-amber-900">
        {fields.map(([name, f]) => (
          <li key={name}>
            <button type="button" className="text-left hover:underline" disabled={!f.document_id}
              onClick={() => f.document_id && onOpenDocument(f.document_id)}>
              {`${FIELD_LABEL[name] ?? name}：從對方書狀「${f.filename ?? ''}」第 ${f.page_no ?? 1} 頁帶入${f.verified ? '（已核對）' : '，請核對'}`}
            </button>
          </li>
        ))}
        {missing.map((name) => <li key={`missing-${name}`}>{`${FIELD_LABEL[name] ?? name}：對方書狀沒寫，請自行填寫`}</li>)}
        {autoParties.map((p) => (
          <li key={p.id}>{`${p.name}（${p.role}）：從對方書狀自動加入${p.auto_verified ? '（已核對）' : '，請核對'}`}</li>
        ))}
      </ul>
      {unverified && canVerify && (
        <button type="button" onClick={() => void verify()}
          className="rounded bg-amber-600 px-3 py-1 text-sm text-white">狀頭核對無誤</button>
      )}
    </section>
  )
}

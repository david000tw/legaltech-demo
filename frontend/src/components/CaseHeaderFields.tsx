// 狀頭欄位（設計 5.2）：案型、法院、案號、股別、案由、（受文）、訴訟代理人、基準日。
// 建立案件與修改狀頭共用；基準日的名稱跟著案型換（例：分割遺產＝繼承開始日）。
import type * as api from '../api'
import type { HeaderDraft } from '../caseForms'

const input = 'mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm'
const cell = 'text-sm text-slate-700'

export default function CaseHeaderFields({ value, onChange, options, addresseeDefault }: {
  value: HeaderDraft
  onChange: (next: HeaderDraft) => void
  options: api.CaseOptions | null
  addresseeDefault?: string  // 有給才顯示「受文」欄（修改狀頭時）
}) {
  const set = (patch: Partial<HeaderDraft>) => onChange({ ...value, ...patch })
  const types = options?.case_types ?? []
  const dateLabel = types.find((t) => t.code === value.case_type)?.reference_date_label ?? '基準日'
  const text = (key: 'court' | 'case_number' | 'division' | 'cause', label: string, max: number, hint = '') => (
    <label className={cell}>{label}
      <input value={value[key]} maxLength={max} placeholder={hint} className={input}
        onChange={(e) => set({ [key]: e.target.value })} />
    </label>
  )

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <label className={cell}>案型
        <select aria-label="案型" value={value.case_type} className={input}
          onChange={(e) => set({ case_type: e.target.value })}>
          {types.length === 0 && <option value={value.case_type}>{value.case_type}</option>}
          {types.map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
        </select>
      </label>
      {text('court', '法院', 100, '例：臺灣臺北地方法院')}
      {text('case_number', '案號', 100, '例：113年度家繼訴字第89號')}
      {text('division', '股別', 50)}
      {text('cause', '案由', 200)}
      <label className={cell}>{dateLabel}
        <input type="date" value={value.reference_date} className={input}
          onChange={(e) => set({ reference_date: e.target.value })} />
      </label>
      <label className={`${cell} sm:col-span-2`}>訴訟代理人（以逗號分隔）
        <input value={value.attorneys} className={input}
          placeholder={addresseeDefault === undefined ? '空白＝帶入建立案件的律師' : '例：王大明律師，李小華律師'}
          onChange={(e) => set({ attorneys: e.target.value })} />
      </label>
      {addresseeDefault !== undefined && (
        <label className={cell}>受文
          <input value={value.addressee} maxLength={200} className={input} placeholder={`空白＝${addresseeDefault}`}
            onChange={(e) => set({ addressee: e.target.value })} />
        </label>
      )}
    </div>
  )
}

// 當事人清單編輯（設計 5.2）：姓名、角色、歸屬、法定代理人、立場說明；可新增、刪除、上移、下移。
// 順序就是狀頭上的排列順序。
import type * as api from '../api'
import type { PartyDraft } from '../caseForms'

const input = 'mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm'
const small = 'rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 disabled:opacity-40'

export default function PartiesEditor({ value, onChange, options }: {
  value: PartyDraft[]
  onChange: (next: PartyDraft[]) => void
  options: api.CaseOptions | null
}) {
  const roles = options?.party_roles ?? ['原告', '被告']
  const alignments = options?.alignments ?? [{ code: 'ours', label: '我方' }, { code: 'opposing', label: '對造' },
    { code: 'aligned', label: '立場與我方相同' }]

  const set = (i: number, patch: Partial<PartyDraft>) => onChange(value.map((p, j) => (j === i ? { ...p, ...patch } : p)))
  const remove = (i: number) => onChange(value.filter((_, j) => j !== i))
  function move(i: number, d: -1 | 1) {
    const next = [...value]
    ;[next[i], next[i + d]] = [next[i + d], next[i]]
    onChange(next)
  }
  // 第一位預設是我方，之後預設對造
  const add = () => onChange([...value, { name: '', role: roles[0], alignment: value.length === 0 ? 'ours' : 'opposing',
    legal_rep: '', position_note: '' }])

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-slate-800">當事人</legend>
      {value.length > 0 && !value.some((p) => p.alignment === 'ours') && (
        <p className="rounded bg-yellow-50 p-2 text-sm text-yellow-800">尚未指定我方當事人，書狀的具狀人與訴訟代理人會是空白</p>
      )}
      {value.map((p, i) => {
        const n = `當事人${i + 1}`
        return (
          <div key={i} className="grid grid-cols-2 gap-2 rounded border border-slate-200 p-2 sm:grid-cols-6">
            <label className="text-xs text-slate-600">姓名
              <input aria-label={`${n} 姓名`} value={p.name} required maxLength={200} className={input}
                onChange={(e) => set(i, { name: e.target.value })} />
            </label>
            <label className="text-xs text-slate-600">角色
              <select aria-label={`${n} 角色`} value={p.role} className={input}
                onChange={(e) => set(i, { role: e.target.value })}>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-600">歸屬
              <select aria-label={`${n} 歸屬`} value={p.alignment} className={input}
                onChange={(e) => set(i, { alignment: e.target.value as api.Alignment })}>
                {alignments.map((a) => <option key={a.code} value={a.code}>{a.label}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-600">法定代理人
              <input aria-label={`${n} 法定代理人`} value={p.legal_rep} maxLength={200} className={input}
                onChange={(e) => set(i, { legal_rep: e.target.value })} />
            </label>
            <label className="col-span-2 text-xs text-slate-600">立場說明
              <input aria-label={`${n} 立場說明`} value={p.position_note} maxLength={1000} className={input}
                placeholder="例：同意分割，但爭執遺產範圍" onChange={(e) => set(i, { position_note: e.target.value })} />
            </label>
            <div className="col-span-2 flex gap-1 sm:col-span-6">
              <button type="button" aria-label={`上移 ${n}`} className={small} disabled={i === 0}
                onClick={() => move(i, -1)}>上移</button>
              <button type="button" aria-label={`下移 ${n}`} className={small} disabled={i === value.length - 1}
                onClick={() => move(i, 1)}>下移</button>
              <button type="button" aria-label={`刪除 ${n}`} className={small} onClick={() => remove(i)}>刪除</button>
            </div>
          </div>
        )
      })}
      <button type="button" onClick={add} className={small}>新增當事人</button>
    </fieldset>
  )
}

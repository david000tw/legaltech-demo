import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import * as api from '../api'
import { emptyHeaderDraft, headerInput, toInputs, type HeaderDraft, type PartyDraft } from '../caseForms'
import CaseHeaderFields from '../components/CaseHeaderFields'
import PartiesEditor from '../components/PartiesEditor'

export default function Cases() {
  const navigate = useNavigate()
  const [cases, setCases] = useState<api.CaseSummary[]>([])
  const [options, setOptions] = useState<api.CaseOptions | null>(null)
  const [title, setTitle] = useState('')
  const [side, setSide] = useState<'plaintiff' | 'defendant'>('plaintiff')
  const [header, setHeader] = useState<HeaderDraft>(emptyHeaderDraft)
  const [parties, setParties] = useState<PartyDraft[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listCases().then(setCases).catch((e: Error) => setError(e.message))
    api.getCaseOptions().then(setOptions).catch((e: Error) => setError(e.message))
  }, [])

  const typeLabel = (code: string) => options?.case_types.find((t) => t.code === code)?.label ?? code

  async function create(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const h = headerInput(header)
      // 訴訟代理人沒填：不送，由後端帶入建立案件的律師
      const c = await api.createCase({ title: title.trim(), our_side: side, ...h,
        attorneys: h.attorneys.length > 0 ? h.attorneys : undefined })
      let notice: string | null = null
      if (parties.length > 0) {
        try {
          await api.putParties(c.id, toInputs(parties))
        } catch (err) {  // 案件已建立：到案件頁補填，不要讓律師重按一次又多建一件
          notice = `案件已建立，但當事人沒有存成功：${(err as Error).message}。請在案件頁重新填寫。`
        }
      }
      navigate(`/cases/${c.id}`, { state: notice ? { notice } : undefined })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <h1 className="text-2xl font-bold text-slate-900">案件</h1>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
        {cases.map((c) => (
          <li key={c.id} className="px-4 py-3">
            <Link to={`/cases/${c.id}`} className="font-medium text-indigo-700 underline">
              {c.title}（{c.our_side === 'plaintiff' ? '原告' : '被告'}）
            </Link>
            <span className="ml-2 text-xs text-slate-500">{typeLabel(c.case_type)}</span>
          </li>
        ))}
        {cases.length === 0 && <li className="px-4 py-3 text-sm text-slate-500">尚無案件</li>}
      </ul>
      <form onSubmit={create} className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-lg font-semibold text-slate-800">建立案件</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm text-slate-700">
            案件名稱
            <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300}
              className="mt-1 block rounded border border-slate-300 px-3 py-1.5" />
          </label>
          <label className="text-sm text-slate-700">
            我方立場
            <select value={side} onChange={(e) => setSide(e.target.value as 'plaintiff' | 'defendant')}
              className="mt-1 block rounded border border-slate-300 px-3 py-1.5">
              <option value="plaintiff">原告</option>
              <option value="defendant">被告</option>
            </select>
          </label>
        </div>
        <CaseHeaderFields value={header} onChange={setHeader} options={options} />
        <PartiesEditor value={parties} onChange={setParties} options={options} />
        <button type="submit" disabled={busy}
          className="rounded bg-indigo-600 px-4 py-1.5 font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
          建立案件
        </button>
      </form>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

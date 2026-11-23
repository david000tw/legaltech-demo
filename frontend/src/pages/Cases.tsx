import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import * as api from '../api'

export default function Cases() {
  const navigate = useNavigate()
  const [cases, setCases] = useState<api.CaseSummary[]>([])
  const [title, setTitle] = useState('')
  const [side, setSide] = useState<'plaintiff' | 'defendant'>('plaintiff')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listCases().then(setCases).catch((e: Error) => setError(e.message))
  }, [])

  async function create(e: FormEvent) {
    e.preventDefault()
    try {
      const c = await api.createCase(title, side)
      navigate(`/cases/${c.id}`)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <h1 className="text-2xl font-bold text-slate-900">案件</h1>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
        {cases.map((c) => (
          <li key={c.id} className="px-4 py-3">
            <Link to={`/cases/${c.id}`} className="font-medium text-indigo-700 underline">
              {c.title}（{c.our_side === 'plaintiff' ? '原告' : '被告'}）
            </Link>
          </li>
        ))}
        {cases.length === 0 && <li className="px-4 py-3 text-sm text-slate-500">尚無案件</li>}
      </ul>
      <form onSubmit={create} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <label className="text-sm text-slate-700">
          案件名稱
          <input value={title} onChange={(e) => setTitle(e.target.value)} required
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
        <button type="submit" className="rounded bg-indigo-600 px-4 py-1.5 font-medium text-white hover:bg-indigo-700">
          建立案件
        </button>
      </form>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

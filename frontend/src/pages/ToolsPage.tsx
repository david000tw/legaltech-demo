import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import * as api from '../api'
import AppShell from '../components/AppShell'
import StepList from '../components/StepList'
import { BTN_SECONDARY, CARD } from '../ui'

const DISCLAIMER = '試算僅供參考，律師須自行核對'
const METHODS: [api.ServiceMethod, string][] = [['direct', '直接送達'], ['deposit', '寄存送達'], ['public', '公示送達'],
  ['foreign_public', '對外國公示送達'], ['continued_public', '依職權續行公示送達']]
const WHO: [api.ServiceInput['who'], string][] = [['party', '本人'], ['attorney', '訴訟代理人']]
const INPUT = 'rounded border border-slate-300 px-2 py-1 text-sm'
const money = (n: number) => n.toLocaleString('en-US')

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className={`${CARD} space-y-3`}>
      <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
      {children}
    </section>
  )
}

function Notes({ notes }: { notes: string[] }) {
  return <ul className="list-disc pl-5 text-xs text-amber-800">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
}

function FeeTool({ initialAmount }: { initialAmount: string }) {
  const [property, setProperty] = useState(true)
  const [amount, setAmount] = useState(initialAmount)
  const [instance, setInstance] = useState<1 | 2 | 3>(1)
  const [appeal, setAppeal] = useState('')
  const [result, setResult] = useState<api.FeeResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const body: api.FeeInput = { property, amount: Number(amount) || 0, instance }
    if (instance > 1 && appeal.trim()) body.appeal_amount = Number(appeal)
    try {
      setResult(await api.courtFee(body))
    } catch (err) {
      setResult(null)
      setError((err as Error).message)
    }
  }

  return (
    <Section title="裁判費">
      <form onSubmit={run} className="flex flex-wrap items-end gap-3 text-sm text-slate-700">
        <label>訴訟種類
          <select value={property ? 'property' : 'non'} onChange={(e) => setProperty(e.target.value === 'property')}
            className={`ml-2 ${INPUT}`}>
            <option value="property">財產權訴訟</option>
            <option value="non">非財產權訴訟</option>
          </select>
        </label>
        {property && (
          <label>訴訟標的金額
            <input type="number" min={0} value={amount} onChange={(e) => setAmount(e.target.value)}
              className={`ml-2 w-40 ${INPUT}`} />
          </label>
        )}
        <label>審級
          <select value={instance} onChange={(e) => setInstance(Number(e.target.value) as 1 | 2 | 3)}
            className={`ml-2 ${INPUT}`}>
            <option value={1}>第一審</option>
            <option value={2}>第二審</option>
            <option value={3}>第三審</option>
          </select>
        </label>
        {property && instance > 1 && (
          <label>上訴金額（部分上訴時填）
            <input type="number" min={0} value={appeal} onChange={(e) => setAppeal(e.target.value)}
              className={`ml-2 w-40 ${INPUT}`} />
          </label>
        )}
        <button type="submit" className={BTN_SECONDARY}>試算</button>
      </form>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {result && (
        <div className="space-y-2">
          {result.fee !== null && <p className="font-medium text-slate-900">{`裁判費：新臺幣${money(result.fee)}元`}</p>}
          <StepList steps={result.steps} />
          <Notes notes={result.notes} />
        </div>
      )}
    </Section>
  )
}

function DeadlineTool({ initialProcedure }: { initialProcedure: api.DeadlineProcedure }) {
  const [kind, setKind] = useState<api.DeadlineInput['kind']>('appeal')
  const [procedure, setProcedure] = useState<api.DeadlineProcedure>(initialProcedure)
  const [customDays, setCustomDays] = useState('')
  const [served, setServed] = useState<Record<string, { served_on: string; method: api.ServiceMethod }>>({
    party: { served_on: '', method: 'direct' }, attorney: { served_on: '', method: 'direct' } })
  const [travel, setTravel] = useState('0')
  const [local, setLocal] = useState(false)
  const [result, setResult] = useState<api.DeadlineResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const setService = (who: string, patch: Partial<{ served_on: string; method: api.ServiceMethod }>) =>
    setServed((s) => ({ ...s, [who]: { ...s[who], ...patch } }))

  async function run(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const services = WHO.filter(([who]) => served[who].served_on)
      .map(([who]) => ({ who, served_on: served[who].served_on, method: served[who].method }))
    if (!services.length) {
      setError('請至少填一筆送達日')
      return
    }
    const body: api.DeadlineInput = { kind, procedure, services, travel_days: Number(travel) || 0, attorney_local: local }
    if (kind === 'custom') body.custom_days = Number(customDays) || 0
    try {
      setResult(await api.deadline(body))
    } catch (err) {
      setResult(null)
      setError((err as Error).message)
    }
  }

  const last = result?.steps[result.steps.length - 1]
  return (
    <Section title="上訴、抗告期限">
      <form onSubmit={run} className="space-y-3 text-sm text-slate-700">
        <div className="flex flex-wrap items-end gap-3">
          <label>種類
            <select value={kind} onChange={(e) => setKind(e.target.value as api.DeadlineInput['kind'])}
              className={`ml-2 ${INPUT}`}>
              <option value="appeal">上訴</option>
              <option value="interlocutory">抗告</option>
              <option value="custom">自訂天數</option>
            </select>
          </label>
          <label>程序
            <select value={procedure} onChange={(e) => setProcedure(e.target.value as api.DeadlineProcedure)}
              className={`ml-2 ${INPUT}`}>
              <option value="civil">民事</option>
              <option value="family_litigation">家事訴訟事件</option>
              <option value="family_nonlitigation">家事非訟事件</option>
            </select>
          </label>
          {kind === 'custom' && (
            <label>天數
              <input type="number" min={1} value={customDays} onChange={(e) => setCustomDays(e.target.value)}
                className={`ml-2 w-20 ${INPUT}`} />
            </label>
          )}
        </div>
        {WHO.map(([who, label]) => (
          <div key={who} className="flex flex-wrap items-center gap-3">
            <span className="w-24 text-slate-600">{label}</span>
            <input type="date" aria-label={`${label} 送達日`} value={served[who].served_on}
              onChange={(e) => setService(who, { served_on: e.target.value })} className={INPUT} />
            <select aria-label={`${label} 送達方式`} value={served[who].method}
              onChange={(e) => setService(who, { method: e.target.value as api.ServiceMethod })} className={INPUT}>
              {METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
        ))}
        <p className="text-xs text-slate-500">本人與訴訟代理人都有送達時，以生效日較早者起算；沒有送達的那一列空著即可。</p>
        <div className="flex flex-wrap items-center gap-3">
          <label>在途期間（日）
            <input type="number" min={0} value={travel} onChange={(e) => setTravel(e.target.value)}
              className={`ml-2 w-20 ${INPUT}`} />
          </label>
          <span className="text-xs text-slate-500">依法院訴訟當事人在途期間標準</span>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={local} onChange={(e) => setLocal(e.target.checked)} />
            訴訟代理人住居法院所在地
          </label>
        </div>
        <button type="submit" className={BTN_SECONDARY}>試算</button>
      </form>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {result && (
        <div className="space-y-2">
          {last && <p className="font-medium text-slate-900">{`屆滿日：${last.value}`}</p>}
          <StepList steps={result.steps} />
          <Notes notes={result.notes} />
        </div>
      )}
    </Section>
  )
}

function CopiesTool({ defaults }: { defaults: api.ToolDefaults | null }) {
  const [copies, setCopies] = useState(defaults?.copies != null ? String(defaults.copies) : '')
  return (
    <Section title="繕本份數">
      <label className="text-sm text-slate-700">份數
        <input type="number" min={1} value={copies} onChange={(e) => setCopies(e.target.value)}
          className={`ml-2 w-20 ${INPUT}`} />
      </label>
      {defaults
        ? <StepList steps={defaults.copies_steps} />
        : <p className="text-xs text-slate-500">從案件頁進來會依當事人自動計算：法院 1 份＋他造各 1 份＋留存 1 份（民事訴訟法第119條）。</p>}
    </Section>
  )
}

// 裁判費與期限試算（設計 5.10）：純計算，不用 AI；從案件頁進來（?case=）會帶入金額、程序與份數
export default function ToolsPage() {
  const [params] = useSearchParams()
  const caseId = params.get('case') ?? undefined
  const [defaults, setDefaults] = useState<api.ToolDefaults | null>(null)
  const [loaded, setLoaded] = useState(!caseId)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!caseId) return
    api.toolDefaults(caseId).then(setDefaults).catch((e: Error) => setError(e.message)).finally(() => setLoaded(true))
  }, [caseId])

  return (
    <AppShell caseId={caseId}>
      <div className="mx-auto max-w-4xl space-y-4 p-6">
        <h1 className="text-2xl font-bold text-slate-900">裁判費與期限試算</h1>
        <p className="rounded bg-amber-50 p-2 text-sm font-medium text-amber-900">{DISCLAIMER}</p>
        {error && <p className="text-sm text-red-700">{error}</p>}
        {loaded && (
          <>
            <FeeTool initialAmount={defaults?.amount != null ? String(defaults.amount) : ''} />
            <DeadlineTool initialProcedure={defaults?.deadline_procedure ?? 'civil'} />
            <CopiesTool defaults={defaults} />
          </>
        )}
      </div>
    </AppShell>
  )
}

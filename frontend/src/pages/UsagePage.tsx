import { useCallback, useEffect, useState, type FormEvent } from 'react'
import * as api from '../api'
import AppShell from '../components/AppShell'
import { useMe } from '../MeContext'
import { BTN_SECONDARY, BTN_SMALL, CARD } from '../ui'

const STEP_LABEL: Record<string, string> = { claims: '拆解主張', requests: '整理請求項目', select: '挑選引用', draft: '起草',
  rewrite: 'AI 重寫段落', evidence_notes: '卷證說明', timeline: '大事記', health: '書狀健檢', ocr: '辨識',
  support: '支持度檢查', judgment_summary: '判決要旨' }
const PRICE_FIELDS: [keyof api.PriceValues, string][] = [['input_usd', '讀入'], ['output_usd', '寫出'],
  ['cache_read_usd', '快取讀'], ['cache_write_5m_usd', '快取寫（5 分鐘）'], ['cache_write_1h_usd', '快取寫（1 小時）']]
const INPUT = 'rounded border border-slate-300 px-2 py-1 text-sm'
const usd = (n: number) => `US$${n.toFixed(4)}`
const twd = (n: number) => `NT$${n.toFixed(2)}`
const num = (n: number) => n.toLocaleString('en-US')

function Filter({ label, value, options, onChange }: {
  label: string; value: string; options: [string, string][]; onChange: (v: string) => void
}) {
  return (
    <label className="text-sm text-slate-700">{label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className={`ml-2 ${INPUT}`}>
        <option value="">全部</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  )
}

function RateForm({ rate, onSaved, onError }: {
  rate: api.RateView; onSaved: () => Promise<void>; onError: (m: string) => void
}) {
  const [value, setValue] = useState(String(rate.usd_twd))
  const [day, setDay] = useState(rate.rate_set_on)
  async function save(e: FormEvent) {
    e.preventDefault()
    try {
      await api.putRate({ usd_twd: Number(value), rate_set_on: day })
      await onSaved()
    } catch (err) {
      onError((err as Error).message)
    }
  }
  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-3 text-sm text-slate-700">
      <label>匯率（1 美元兌新台幣）
        <input type="number" min={0} step="0.0001" value={value} onChange={(e) => setValue(e.target.value)}
          className={`ml-2 w-28 ${INPUT}`} />
      </label>
      <label>匯率設定日期
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} className={`ml-2 ${INPUT}`} />
      </label>
      <button type="submit" disabled={!(Number(value) > 0) || !day} className={BTN_SECONDARY}>儲存匯率</button>
      {rate.is_default && <span className="text-xs text-amber-700">預設值，請依實際匯率修改</span>}
    </form>
  )
}

function priceValues(p: api.Price): api.PriceValues {
  return { input_usd: p.input_usd, output_usd: p.output_usd, cache_read_usd: p.cache_read_usd,
    cache_write_5m_usd: p.cache_write_5m_usd, cache_write_1h_usd: p.cache_write_1h_usd, billing: p.billing, note: p.note }
}

function PriceEditor({ initial, onSave, onCancel, withKey }: {
  initial: Omit<api.Price, 'id'>; onSave: (p: Omit<api.Price, 'id'>) => Promise<void>; onCancel: () => void
  withKey: boolean
}) {
  const [v, setV] = useState(initial)
  const set = (patch: Partial<Omit<api.Price, 'id'>>) => setV((x) => ({ ...x, ...patch }))
  return (
    <tr className="bg-amber-50 align-top">
      <td className="p-1" colSpan={2}>
        {withKey ? (
          <div className="flex flex-col gap-1">
            <input aria-label="來源" value={v.source} onChange={(e) => set({ source: e.target.value })} className={INPUT} />
            <input aria-label="模型" value={v.model} onChange={(e) => set({ model: e.target.value })} className={INPUT} />
          </div>
        ) : `${v.source} ${v.model}`}
      </td>
      {PRICE_FIELDS.map(([k, label]) => (
        <td key={k} className="p-1">
          <input type="number" min={0} step="0.0001" aria-label={`${label}（每百萬 token，美元）`} value={v[k] as number}
            onChange={(e) => set({ [k]: Number(e.target.value) })} className={`w-20 ${INPUT}`} />
        </td>
      ))}
      <td className="p-1">
        <select aria-label="計費" value={v.billing} onChange={(e) => set({ billing: e.target.value as api.Price['billing'] })}
          className={INPUT}>
          <option value="metered">按量</option>
          <option value="unmetered">不按量</option>
        </select>
      </td>
      <td className="p-1">
        <input aria-label="備註" value={v.note} onChange={(e) => set({ note: e.target.value })} className={`w-40 ${INPUT}`} />
      </td>
      <td className="space-x-1 p-1 whitespace-nowrap">
        <button type="button" onClick={() => void onSave(v)} className={BTN_SMALL}>儲存單價</button>
        <button type="button" onClick={onCancel} className={BTN_SMALL}>取消</button>
      </td>
    </tr>
  )
}

function PriceTable({ prices, admin, onChanged, onError }: {
  prices: api.Price[]; admin: boolean; onChanged: () => Promise<void>; onError: (m: string) => void
}) {
  const [editing, setEditing] = useState<string | null>(null)  // 單價 id；'new'＝新增
  async function save(fn: () => Promise<unknown>) {
    try {
      await fn()
      setEditing(null)
      await onChanged()
    } catch (e) {
      onError((e as Error).message)
    }
  }
  const blank: Omit<api.Price, 'id'> = { source: '', model: '', input_usd: 0, output_usd: 0, cache_read_usd: 0,
    cache_write_5m_usd: 0, cache_write_1h_usd: 0, billing: 'metered', note: '' }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table aria-label="單價" className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-slate-500">
              <th className="p-1 font-normal">來源</th><th className="p-1 font-normal">模型</th>
              {PRICE_FIELDS.map(([k, label]) => <th key={k} className="p-1 font-normal">{label}</th>)}
              <th className="p-1 font-normal">計費</th><th className="p-1 font-normal">備註</th>
              {admin && <th className="p-1 font-normal" />}
            </tr>
          </thead>
          <tbody>
            {prices.map((p) => editing === p.id ? (
              <PriceEditor key={p.id} initial={p} withKey={false} onCancel={() => setEditing(null)}
                onSave={(v) => save(() => api.putPrice(p.id, priceValues({ ...v, id: p.id })))} />
            ) : (
              <tr key={p.id} className="border-b border-slate-100">
                <td className="p-1">{p.source}</td><td className="p-1">{p.model}</td>
                {PRICE_FIELDS.map(([k]) => <td key={k} className="p-1">{p[k] as number}</td>)}
                <td className="p-1">{p.billing === 'metered' ? '按量' : '不按量'}</td>
                <td className="p-1 text-xs text-slate-600">{p.note}</td>
                {admin && (
                  <td className="p-1">
                    <button type="button" aria-label={`修改 ${p.source} ${p.model}`} onClick={() => setEditing(p.id)}
                      className={BTN_SMALL}>修改</button>
                  </td>
                )}
              </tr>
            ))}
            {editing === 'new' && (
              <PriceEditor initial={blank} withKey onCancel={() => setEditing(null)}
                onSave={(v) => save(() => api.postPrice(v))} />
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">單價為每百萬 token 的美元。</p>
      {admin
        ? editing !== 'new' && <button type="button" onClick={() => setEditing('new')} className={BTN_SECONDARY}>新增單價</button>
        : <p className="text-xs text-slate-500">單價限事務所管理員修改</p>}
    </div>
  )
}

// 用量與費用（設計 5.12）：每次 AI 呼叫（含失敗與內部重試）的 token 用量，依單價與匯率換算
export default function UsagePage() {
  const me = useMe()
  const [filters, setFilters] = useState<api.UsageFilters>({})
  const [summary, setSummary] = useState<api.UsageSummary | null>(null)
  const [prices, setPrices] = useState<api.Price[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setSummary(await api.getUsage(filters))
      setError(null)  // 這次讀取成功：先前讀取失敗的訊息不再適用
    } catch (e) {
      setError((e as Error).message)
    }
  }, [filters])
  const loadPrices = useCallback(async () => {
    try {
      setPrices(await api.getPrices())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { void loadPrices() }, [loadPrices])

  const setFilter = (key: keyof api.UsageFilters) => (value: string) => setFilters((f) => {
    const next = { ...f }
    if (value) next[key] = value
    else delete next[key]
    return next
  })

  const f = summary?.filters
  return (
    <AppShell>
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <h1 className="text-2xl font-bold text-slate-900">用量與費用</h1>
        {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        {summary && f && (
          <>
            <div className="flex flex-wrap gap-3">
              <Filter label="月份" value={filters.month ?? ''} options={f.months.map((m) => [m, m])} onChange={setFilter('month')} />
              <Filter label="案件" value={filters.case_id ?? ''} options={f.cases.map((c) => [c.id, c.title])}
                onChange={setFilter('case_id')} />
              <Filter label="步驟" value={filters.step ?? ''} options={f.steps.map((s) => [s, STEP_LABEL[s] ?? s])}
                onChange={setFilter('step')} />
              <Filter label="來源" value={filters.source ?? ''} options={f.sources.map((s) => [s, s])} onChange={setFilter('source')} />
              <Filter label="模型" value={filters.model ?? ''} options={f.models.map((m) => [m, m])} onChange={setFilter('model')} />
            </div>
            <section className={`${CARD} overflow-x-auto`}>
              <table aria-label="用量" className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    {['來源', '模型', '步驟', '呼叫次數（含失敗與重試）', '讀入', '寫出', '快取讀', '快取寫', '美元', '新台幣']
                      .map((h) => <th key={h} className="p-1 font-normal">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {summary.rows.map((r) => (
                    <tr key={`${r.source}-${r.model}-${r.step}`} className="border-b border-slate-100">
                      <td className="p-1">{r.source}{r.billing === 'unmetered' && <span className="ml-1 text-xs text-slate-500">（不按量）</span>}</td>
                      <td className="p-1">{r.model}</td>
                      <td className="p-1">{STEP_LABEL[r.step] ?? r.step}</td>
                      <td className="p-1">{r.failed ? `${r.calls}（失敗 ${r.failed}）` : `${r.calls}`}</td>
                      <td className="p-1">{num(r.input)}</td>
                      <td className="p-1">{num(r.output)}</td>
                      <td className="p-1">{num(r.cache_read)}</td>
                      <td className="p-1">{num(r.cache_write)}</td>
                      <td className="p-1">{r.usd !== null ? usd(r.usd) : <span className="text-amber-700">未設定單價</span>}</td>
                      <td className="p-1">{r.twd !== null ? twd(r.twd) : '—'}</td>
                    </tr>
                  ))}
                  {summary.rows.length === 0 && (
                    <tr><td colSpan={10} className="p-2 text-slate-500">這個範圍沒有 AI 呼叫紀錄</td></tr>
                  )}
                </tbody>
                <tfoot>
                  <tr className="font-medium">
                    <td colSpan={8} className="p-1 text-right">按量合計</td>
                    <td className="p-1">{usd(summary.totals.metered.usd)}</td>
                    <td className="p-1">{twd(summary.totals.metered.twd)}</td>
                  </tr>
                  <tr className="text-slate-600">
                    <td colSpan={8} className="p-1 text-right">不按量合計（不按量計費，換算僅供參考）</td>
                    <td className="p-1">{usd(summary.totals.unmetered.usd)}</td>
                    <td className="p-1">{twd(summary.totals.unmetered.twd)}</td>
                  </tr>
                </tfoot>
              </table>
              {summary.notes.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-xs text-amber-800">{summary.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              )}
            </section>
            <section className={`${CARD} space-y-2`}>
              <h2 className="text-lg font-semibold text-slate-800">匯率</h2>
              <RateForm key={`${summary.rate.usd_twd}-${summary.rate.rate_set_on}`} rate={summary.rate} onSaved={load}
                onError={setError} />
            </section>
          </>
        )}
        {summary && prices && (
          <section className={`${CARD} space-y-2`}>
            <h2 className="text-lg font-semibold text-slate-800">單價</h2>
            <PriceTable prices={prices} admin={me?.role === 'firm_admin'} onError={setError}
              onChanged={async () => { await loadPrices(); await load() }} />
          </section>
        )}
      </div>
    </AppShell>
  )
}

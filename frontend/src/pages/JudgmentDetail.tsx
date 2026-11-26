import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import * as api from '../api'
import AppShell from '../components/AppShell'
import { IssueTags } from '../components/JudgmentSearchTab'
import { citedPath, courtLine, importHint, judgmentPath, searchByArticlePath } from '../judgmentLinks'
import { BTN_SECONDARY, BTN_SMALL, CARD } from '../ui'

const POLL_MS = 3_000
const POLL_LIMIT = 200  // 3 秒 × 200 次＝10 分鐘，大於後端的卡住判定（9 分鐘）

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className={`${CARD} space-y-2`}>
      <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
      {children}
    </section>
  )
}

function marked(text: string, span: [number, number] | null): ReactNode {
  if (!span) return text
  const [a, b] = span
  return <>{text.slice(0, a)}<mark className="bg-yellow-200">{text.slice(a, b)}</mark>{text.slice(b)}</>
}

// 要旨：沒有或過期就請伺服器產生一次；產生中每 3 秒輪詢，輪詢中發現卡住（stale）會再請一次；10 分鐘還沒好就停，給「重試」
function useSummary(caseId: string, judgmentId: string, initial: api.SummaryView) {
  const [view, setView] = useState<api.SummaryView>(initial)
  const [tooLong, setTooLong] = useState(false)
  const polls = useRef(0)
  const requested = useRef(false)

  const request = useCallback(async (retry: boolean) => {
    polls.current = 0
    setTooLong(false)
    try {
      setView(await api.requestSummary(caseId, judgmentId, retry))
    } catch (e) {
      setView({ status: 'failed', issues: [], excerpts: [], message: (e as Error).message })
    }
  }, [caseId, judgmentId])

  useEffect(() => {
    if ((view.status === 'none' || view.status === 'stale') && !requested.current) {
      requested.current = true
      void request(false)
      return
    }
    if (view.status !== 'generating' && view.status !== 'stale') return
    if (polls.current >= POLL_LIMIT) {
      setTooLong(true)
      return
    }
    const t = setTimeout(async () => {
      polls.current += 1
      try {
        const next = await api.judgmentSummary(caseId, judgmentId)
        // 每次都換新物件：狀態沒變（仍在產生中）時 effect 才會再排下一次輪詢
        setView(next.status === 'stale' ? { ...(await api.requestSummary(caseId, judgmentId, false)) } : { ...next })
      } catch {
        setView({ ...view })  // 暫時連不上：下一輪再試
      }
    }, POLL_MS)
    return () => clearTimeout(t)
  }, [view, caseId, judgmentId, request])

  return { view, tooLong, retry: () => request(true) }
}

// 要旨區：判決讀進來後才掛上，以判決內的要旨狀態起始（不另外同步狀態）
function SummaryBlock({ caseId, judgmentId, initial, onExcerpt }: {
  caseId: string; judgmentId: string; initial: api.SummaryView; onExcerpt: (e: api.SummaryExcerpt) => void
}) {
  const { view, tooLong, retry: onRetry } = useSummary(caseId, judgmentId, initial)
  const retry = <button type="button" onClick={() => void onRetry()} className={BTN_SMALL}>重試</button>
  if (tooLong) return <p className="flex items-center gap-2 text-sm text-amber-800">要旨產生時間過長{retry}</p>
  if (view.status === 'none' || view.status === 'stale' || view.status === 'generating') {
    return <p className="text-sm text-slate-500">要旨產生中…</p>
  }
  if (view.status === 'failed') {
    return <p className="flex items-center gap-2 text-sm text-red-700">{view.message ?? '要旨產生失敗'}{retry}</p>
  }
  if (view.status === 'unavailable') return <p className="text-sm text-slate-600">{view.message}</p>
  return (
    <div className="space-y-2">
      <IssueTags issues={view.issues} />
      <p className="text-xs text-slate-500">以下是判決原文節錄，點一下會在全文裡標出位置。</p>
      <ul className="space-y-1">
        {view.excerpts.map((e) => (
          <li key={`${e.passage_seq}-${e.span[0]}`}>
            <button type="button" onClick={() => onExcerpt(e)}
              className="text-left text-sm leading-7 text-slate-800 underline decoration-dotted">{e.text}</button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// 整份判決（計畫 3 丙，放在案件裡）：要旨、引用的條文、引用的判決、被誰引用、全文（法院判斷段可以釘選）
export default function JudgmentDetail() {
  const { caseId = '', judgmentId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<api.JudgmentDetailView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mark, setMark] = useState<{ seq: number; span: [number, number] } | null>(null)
  const [version, setVersion] = useState(0)  // 釘選後重新讀取

  useEffect(() => {
    let alive = true
    api.judgmentDetail(caseId, judgmentId)
      .then((d) => { if (alive) { setDetail(d); setError(null) } })
      .catch((e: Error) => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [caseId, judgmentId, version])

  function showExcerpt(e: api.SummaryExcerpt) {
    setMark({ seq: e.passage_seq, span: e.span })
    document.getElementById(`passage-${e.passage_seq}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }
  async function pin(p: api.JudgmentPassageView) {
    try {
      await api.pinPassage(caseId, p.passage_id, '')
      setVersion((v) => v + 1)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  function copy(text: string) {
    navigator.clipboard?.writeText(text).catch(() => undefined)
  }

  return (
    <AppShell caseId={caseId}>
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Link to={`/cases/${caseId}?tab=search`} className="text-sm text-blue-700 underline">回判決檢索</Link>
        {error && <p className="text-sm text-red-700">{error}</p>}
        {detail && (
          <>
            <header className="space-y-1">
              <h1 className="text-xl font-semibold text-slate-900">{detail.citation}</h1>
              <p className="text-sm text-slate-600">
                {courtLine(detail.court, detail.decided_on, detail.case_type_labels)}・
                <a href={detail.source_url} target="_blank" rel="noreferrer" className="text-blue-700 underline">司法院原文</a>
              </p>
              {detail.annotation && (
                <p className="rounded border border-purple-300 bg-purple-50 p-2 text-xs text-purple-900">{detail.annotation}</p>
              )}
            </header>

            <Section title="要旨">
              <SummaryBlock key={detail.id} caseId={caseId} judgmentId={judgmentId} initial={detail.summary}
                onExcerpt={showExcerpt} />
            </Section>

            <Section title="引用的條文">
              {detail.articles.length === 0 ? <p className="text-sm text-slate-500">法院理由沒有引用條文</p> : (
                <div className="flex flex-wrap gap-2">
                  {detail.articles.map((a) => (
                    <button key={a.display} type="button" className={BTN_SMALL}
                      onClick={() => navigate(searchByArticlePath(caseId, a.query))}>{a.display}</button>
                  ))}
                </div>
              )}
            </Section>

            <Section title="引用的判決">
              {detail.cites.length === 0 ? <p className="text-sm text-slate-500">法院理由沒有引用其他判決</p> : (
                <ul className="space-y-2 text-sm">
                  {detail.cites.map((c) => (
                    <li key={c.key} className="space-y-1">
                      {c.library.length > 0 ? (
                        <span className="flex flex-wrap gap-2">
                          {c.library.map((l) => (
                            <Link key={l.judgment_id} to={judgmentPath(caseId, l.judgment_id)} className="text-blue-700 underline">
                              {l.citation}
                            </Link>
                          ))}
                        </span>
                      ) : (
                        <>
                          <p className="flex flex-wrap items-center gap-2">
                            <span className="text-slate-800">{c.display}</span>
                            <Link to={citedPath(caseId, c.key)} className="text-blue-700 underline">{`庫裡有 ${c.citing_count} 份判決引用`}</Link>
                            <button type="button" onClick={() => copy(c.display)} className={BTN_SMALL}>複製字號</button>
                          </p>
                          <p className="text-xs text-slate-500">{importHint(c.supreme, detail.supreme_importable)}</p>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="被誰引用">
              {detail.cited_by.length === 0 ? <p className="text-sm text-slate-500">庫裡還沒有判決引用這一份</p> : (
                <ul className="space-y-2 text-sm">
                  {detail.cited_by.map((c) => (
                    <li key={c.judgment_id}>
                      <Link to={judgmentPath(caseId, c.judgment_id)} className="text-blue-700 underline">{c.citation}</Link>
                      <p className="text-slate-700">{c.sentence}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="全文">
              <ol className="space-y-3">
                {detail.passages.map((p) => {
                  const party = p.passage_type === 'party_claim'
                  return (
                    <li key={p.passage_id} id={`passage-${p.seq}`} className={party ? 'text-slate-400' : ''}>
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <span className="text-slate-500">{p.label}</span>
                        {party && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">當事人主張，不是法院見解</span>}
                        {p.passage_type === 'court_reasoning' && (p.pin_id
                          ? <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-800">已釘選</span>
                          : <button type="button" onClick={() => void pin(p)} className={BTN_SECONDARY}>釘選</button>)}
                      </div>
                      <p className="whitespace-pre-wrap text-sm leading-7">
                        {mark && mark.seq === p.seq ? marked(p.text, mark.span) : p.text}
                      </p>
                    </li>
                  )
                })}
              </ol>
            </Section>
          </>
        )}
      </div>
    </AppShell>
  )
}

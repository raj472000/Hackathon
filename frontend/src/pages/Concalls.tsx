import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, post, useAction, useApi, waitForJob } from '../api/client'
import { InstrumentSearch } from '../components/InstrumentSearch'
import { Async, Badge, Button, EmptyState, ErrorState, Missing, Notice, PageHeader, Panel, RefreshButton, StateBadge } from '../components/ui'
import { fmtDate, fmtDateTime, fmtNum } from '../lib/format'

const VERDICT: Record<string, { label: string; tone: string; mark: string }> = {
  delivered: { label: 'Delivered', tone: 'good', mark: '✓' },
  partially_delivered: { label: 'Partly delivered', tone: 'warn', mark: '◐' },
  in_progress: { label: 'In progress', tone: 'info', mark: '→' },
  deferred: { label: 'Deferred', tone: 'warn', mark: '⏸' },
  not_delivered: { label: 'Not delivered', tone: 'bad', mark: '✕' },
  not_addressed: { label: 'Not addressed', tone: 'neutral', mark: '∅' },
}

function ScoreDial({ score }: { score: any }) {
  const v = score.value
  return (
    <div className="flex items-center gap-4">
      <div>
        <div className="text-xs text-ink-2">Follow-through score</div>
        {v == null ? (
          <div className="text-lg">
            <Missing reason={`Fewer than ${score.min_commitments} checkable commitments were found`} /> Not enough to score
          </div>
        ) : (
          <div>
            <span className="num font-display text-[40px] font-semibold leading-none">{Math.round(v)}</span>
            <span className="ml-1 text-ink-2">/ 100</span>
          </div>
        )}
      </div>
      <div className="min-w-[180px] flex-1">
        <div className="h-2.5 overflow-hidden rounded-full bg-[var(--glyph-empty)]" aria-hidden>
          {v != null && <div className="h-full rounded-full bg-[var(--glyph)]" style={{ width: `${v}%` }} />}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <Badge tone={v == null ? 'neutral' : v >= 75 ? 'good' : v >= 45 ? 'warn' : 'bad'}>{score.label}</Badge>
          <StateBadge state={score.confidence} label={`Confidence: ${score.confidence}`} />
        </div>
      </div>
    </div>
  )
}

function Analysis({ d }: { d: any }) {
  const sc = d.score
  return (
    <div className="space-y-4">
      <Panel
        title={`${d.previous.label} commitments checked against ${d.current.label}${d.comparison && !d.comparison.consecutive ? ' (not consecutive)' : ''}`}
        aside={
          <>
            <Badge tone={d.mode === 'llm' ? 'accent' : 'neutral'}>{d.mode === 'llm' ? `Classified by ${d.llm.model_id}` : 'Keyword heuristic, no language model'}</Badge>
            {d.created_at && <span>Run {fmtDateTime(d.created_at)}</span>}
          </>
        }
      >
        {d.comparison?.warning && (
          <div className="px-4 pt-3" data-testid="concall-quarter-warning">
            <Notice tone="warn" title={`Compared with ${d.previous.label} because ${d.comparison.requested_previous} is missing.`}>
              {d.comparison.warning}
            </Notice>
          </div>
        )}
        <div className="grid gap-4 px-4 py-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <ScoreDial score={sc} />
          <div className="text-[13px] text-ink-2">
            <p className="m-0">
              {sc.assessed} commitment{sc.assessed === 1 ? '' : 's'} scored
              {sc.excluded_ungrounded > 0 && `, ${sc.excluded_ungrounded} excluded because a quote was not found in the transcript`}. Score = {sc.formula}.
            </p>
            <p className="m-0 mt-1">
              Credit: {Object.entries(sc.credit_table).map(([k, v]) => `${VERDICT[k]?.label ?? k} ${v}`).join(', ')}. Importance weight: {Object.entries(sc.importance_weights).map(([k, v]) => `${k} ${v}`).join(', ')}.
            </p>
            <ul className="m-0 mt-2 flex list-none flex-wrap gap-1.5 p-0">
              {Object.entries(sc.counts).map(([k, n]) => (
                <li key={k}>
                  <Badge tone={VERDICT[k]?.tone}>
                    <span aria-hidden>{VERDICT[k]?.mark}</span> {VERDICT[k]?.label ?? k}: {n as number}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        </div>
        {d.mode !== 'llm' && d.llm.reason && (
          <div className="px-4 pb-3">
            <Notice tone="info" title="Language model not used.">
              {d.llm.reason}
            </Notice>
          </div>
        )}
        {d.web?.available && (
          <p className="m-0 px-4 pb-3 text-[13px] text-ink-2">
            Web check: {d.web.queries} searches through {d.web.provider}, {d.web.results} sources retrieved
            {d.web.retrieved_at && <> on {fmtDateTime(d.web.retrieved_at)}</>}.
          </p>
        )}
        {d.limitations.filter((l: string) => l !== d.comparison?.warning).map((l: string) => (
          <div key={l} className="px-4 pb-3">
            <Notice tone="warn">{l}</Notice>
          </div>
        ))}
      </Panel>
      <Panel title="Commitment by commitment">
        {d.assessments.length === 0 ? (
          <EmptyState title="No checkable commitments were found">The previous call contained no forward-looking statement specific enough to verify.</EmptyState>
        ) : (
          <ol className="m-0 list-none p-0">
            {d.assessments.map((a: any) => {
              const v = VERDICT[a.verdict] ?? { label: 'No assessment', tone: 'neutral', mark: '?' }
              return (
                <li key={a.id} className="grid gap-x-6 gap-y-2 border-b border-rule px-4 py-3 last:border-0 lg:grid-cols-2">
                  <div>
                    <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
                      <Badge>{a.category}</Badge>
                      <Badge tone={a.importance === 'high' ? 'accent' : 'neutral'}>{a.importance} importance</Badge>
                      <span>Said in {d.previous.label}</span>
                    </div>
                    <blockquote className="m-0 border-l-2 border-rule-strong pl-3">“{a.quote}”</blockquote>
                    {a.target && <p className="m-0 mt-1 text-[13px] text-ink-2">Target: {a.target}</p>}
                  </div>
                  <div>
                    <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
                      <Badge tone={v.tone}>
                        <span aria-hidden>{v.mark}</span> {v.label}
                      </Badge>
                      <span>{a.evidence_source === 'web' ? 'Evidence from the web' : `Evidence from ${d.current.label}`}</span>
                      {!a.grounded && <Badge tone="bad">Excluded from score</Badge>}
                    </div>
                    {a.evidence_quote ? (
                      <blockquote className="m-0 border-l-2 border-rule-strong pl-3">“{a.evidence_quote}”</blockquote>
                    ) : a.evidence_source === 'web' && a.web_sources?.length > 0 ? (
                      <ul className="m-0 list-none space-y-1 p-0" data-testid="concall-web-evidence">
                        {a.web_sources.map((w: any) => (
                          <li key={w.id} className="border-l-2 border-accent pl-3">
                            <a href={w.url} target="_blank" rel="noopener noreferrer nofollow">
                              {w.title}
                            </a>
                            <span className="ml-1 text-xs text-ink-2">
                              web · {w.published_at ? fmtDate(w.published_at) : 'undated'}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="m-0 text-ink-2">{a.verdict === 'not_addressed' ? 'The current call does not mention this.' : (a.grounding_note ?? 'No supporting quote.')}</p>
                    )}
                    <p className="m-0 mt-1 text-[13px] text-ink-2">
                      {a.rationale}
                      {a.grounding_note && ` Note: ${a.grounding_note}.`}
                    </p>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </Panel>
      <p className="m-0 max-w-[90ch] text-xs text-ink-2">{d.disclaimer}</p>
    </div>
  )
}

function Upload({ refId, onDone }: { refId: string; onDone: () => void }) {
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState('')
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const fd = new FormData(form)
    const file = fd.get('file') as File | null
    if (file && file.size === 0) fd.delete('file')
    if (!fd.get('text')) fd.delete('text')
    if (!fd.get('source_url')) fd.delete('source_url')
    if (!fd.get('call_date')) fd.delete('call_date')
    setBusy(true)
    setErr(null)
    setOk('')
    try {
      const r = await api(`/instruments/${refId}/concalls`, { method: 'POST', body: fd })
      setOk(`Saved ${r.transcript.label} (${fmtNum(r.transcript.chars, 0)} characters).`)
      form.reset()
      onDone()
    } catch (x) {
      setErr(x)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-3 px-4 py-4 sm:grid-cols-2">
      <label className="block">
        <span className="mb-1 block font-medium">Fiscal year</span>
        <input name="fiscal_year" type="number" min={2000} max={2100} required className="field w-full" placeholder="2027" />
      </label>
      <label className="block">
        <span className="mb-1 block font-medium">Quarter</span>
        <select name="quarter" required className="field w-full">
          {[1, 2, 3, 4].map((q) => (
            <option key={q} value={q}>
              Q{q}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="mb-1 block font-medium">Source</span>
        <input name="source" required minLength={2} maxLength={200} className="field w-full" placeholder="Company investor-relations transcript" />
      </label>
      <label className="block">
        <span className="mb-1 block font-medium">Source link (optional)</span>
        <input name="source_url" type="url" className="field w-full" placeholder="https://" />
      </label>
      <label className="block">
        <span className="mb-1 block font-medium">Call date (optional)</span>
        <input name="call_date" type="date" className="field w-full" />
      </label>
      <label className="block">
        <span className="mb-1 block font-medium">Transcript file (.pdf or .txt, up to 8 MB)</span>
        <input name="file" type="file" accept=".pdf,.txt,text/plain,application/pdf" className="field w-full" />
      </label>
      <label className="block sm:col-span-2">
        <span className="mb-1 block font-medium">Or paste the transcript text</span>
        <textarea name="text" rows={4} className="field w-full" />
      </label>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
        <Button variant="primary" type="submit" busy={busy}>
          Save transcript
        </Button>
        <span className="text-[13px] text-ink-2">Saving a quarter that already exists replaces its text.</span>
        {ok && <span className="text-gain">{ok}</span>}
      </div>
      {err != null && (
        <div className="sm:col-span-2">
          <ErrorState error={err} />
        </div>
      )}
    </form>
  )
}

function useAutoAnalysis(refStr: string) {
  const refId = encodeURIComponent(refStr)
  const qc = useQueryClient()
  const [state, setState] = useState<{ busy: boolean; step: string; error: string | null; result: any }>({ busy: false, step: '', error: null, result: null })
  const start = async () => {
    setState({ busy: true, step: 'Fetching con-call transcripts filed with NSE…', error: null, result: null })
    try {
      const { job_id } = await post(`/instruments/${refId}/concalls/auto`, {})
      const j = await waitForJob(job_id, () => setState((s) => ({ ...s, step: 'Reading the calls, checking each promise on the web and scoring…' })))
      if (j.status !== 'succeeded') throw new Error(j.error ?? `The analysis ${j.status}.`)
      setState({ busy: false, step: '', error: null, result: j.detail })
    } catch (e: any) {
      setState({ busy: false, step: '', error: e?.message ?? 'The analysis failed.', result: null })
    } finally {
      qc.invalidateQueries({ queryKey: ['concalls', refStr] })
      qc.invalidateQueries({ queryKey: ['concall-overview'] })
    }
  }
  return { ...state, start }
}

function ForInstrument({ refStr }: { refStr: string }) {
  const refId = encodeURIComponent(refStr)
  const qc = useQueryClient()
  const q = useApi<any>(['concalls', refStr], `/instruments/${refId}/concalls`)
  const auto = useAutoAnalysis(refStr)
  const run = useAction<void, any>(() => post(`/instruments/${refId}/concalls/analyze`, {}), [['concalls', refStr], ['concall-overview']])
  const started = useRef(false)
  // Asking about a company is enough: with no analysis on file, fetch its calls from NSE and score them.
  useEffect(() => {
    if (!started.current && q.data && !q.data.latest && q.data.nse_filings?.available && !auto.busy) {
      started.current = true
      auto.start()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data])
  return (
    <Async q={q} rows={10}>
      {(d) => (
        <div className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="mb-1 text-[13px]">
                <Link to="/concalls">Concall follow-through</Link> <span className="text-ink-3">/</span> <Link to={`/instrument/${refStr}`}>{d.instrument.symbol}</Link>
              </p>
              <h1 className="text-[26px] font-semibold leading-tight">
                {d.instrument.symbol} <span className="font-normal text-ink-2">{d.instrument.name}</span>
              </h1>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <RefreshButton keys={[['concalls', refStr]]} />
              {d.transcripts.length >= 2 && (
                <Button onClick={() => run.mutate()} busy={run.isPending} disabled={auto.busy} title="Score the transcripts already stored, without fetching from NSE">
                  Score stored calls
                </Button>
              )}
              <Button variant="primary" onClick={auto.start} busy={auto.busy} disabled={!d.nse_filings?.available} data-testid="concall-auto"
                title={d.nse_filings?.available ? 'Fetch the latest transcripts from NSE, check promises on the web, and score' : d.nse_filings?.reason}>
                {d.latest ? 'Fetch latest from NSE and re-score' : 'Fetch from NSE and analyse'}
              </Button>
            </div>
          </div>
          {auto.busy && (
            <div role="status" data-testid="concall-progress">
              <Notice tone="info" title="Working on it.">
                {auto.step}
              </Notice>
            </div>
          )}
          {run.error && <ErrorState error={run.error} />}
          {auto.error && (
            <Notice tone="bad" title="The analysis could not be completed.">
              {auto.error}
            </Notice>
          )}
          {auto.result && (
            <Notice tone="good" title={`Scored ${auto.result.previous} → ${auto.result.current}.`}>
              {(() => {
                const n = auto.result.fetched.stored.filter((t: any) => t.fetched).length
                return n > 0
                  ? `${n} new transcript${n === 1 ? '' : 's'} fetched from NSE (${auto.result.fetched.found} found).`
                  : `All ${auto.result.fetched.found} transcripts filed with NSE were already stored.`
              })()}
              {auto.result.fetched.skipped.length > 0 && ` Skipped: ${auto.result.fetched.skipped.map((x: any) => `${x.label} (${x.reason})`).join('; ')}.`}
              {auto.result.warning && <span className="mt-1 block font-medium text-warn">Warning: {auto.result.warning}</span>}
            </Notice>
          )}
          {!d.nse_filings?.available && (
            <Notice tone="warn" title="NSE filings are not configured.">
              {d.nse_filings?.reason}. Set NSE_FILINGS_BASE_URL in .env to fetch transcripts automatically, or add them below.
            </Notice>
          )}
          {d.latest ? <Analysis d={d.latest} /> : !auto.busy && d.transcripts.length >= 2 && <Panel><EmptyState title="Transcripts are ready">Use “Fetch from NSE and analyse” to score follow-through.</EmptyState></Panel>}
          <Panel title="Transcripts on file">
            {d.transcripts.length === 0 ? (
              <EmptyState title="No transcripts yet">{auto.busy ? 'Fetching from NSE…' : d.data_state_reason}</EmptyState>
            ) : (
              <table className="text-[13px]">
                <thead>
                  <tr>
                    <th>Quarter</th>
                    <th>Source</th>
                    <th className="r">Length</th>
                    <th>Filed or added</th>
                  </tr>
                </thead>
                <tbody>
                  {d.transcripts.map((t: any) => (
                    <tr key={t.id}>
                      <td className="font-medium">{t.label}</td>
                      <td>
                        {t.source_url ? (
                          <a href={t.source_url} target="_blank" rel="noopener noreferrer nofollow">
                            {t.source}
                          </a>
                        ) : (
                          t.source
                        )}
                        {t.is_demo && <div className="text-xs text-ink-2">Synthetic demo transcript</div>}
                      </td>
                      <td className="r num">{fmtNum(t.chars, 0)}</td>
                      <td>{fmtDate(t.call_date ?? t.uploaded_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
          <details className="rounded-[10px] border border-rule bg-surface">
            <summary className="cursor-pointer px-4 py-3 font-medium">Add a call that NSE does not have</summary>
            <Upload refId={refId} onDone={() => qc.invalidateQueries({ queryKey: ['concalls', refStr] })} />
          </details>
        </div>
      )}
    </Async>
  )
}

export function Concalls() {
  const { ref } = useParams()
  const nav = useNavigate()
  const q = useApi<any>(['concall-overview'], ref ? null : '/concalls')
  if (ref) return <ForInstrument refStr={ref} />
  return (
    <>
      <PageHeader
        title="Concall follow-through"
        lede="Did management do this quarter what it said last quarter? Calls are fetched from NSE filings, promises from the previous call are checked against the current call and the web, and scored."
        refresh={<RefreshButton keys={[['concall-overview'], ['concalls']]} />}
      />
      <div className="mb-4 max-w-md">
        <InstrumentSearch equitiesOnly placeholder="Choose a company: its calls are fetched from NSE and scored" onPick={(i) => nav(`/concalls/${i.exchange}:${encodeURIComponent(i.symbol)}`)} />
      </div>
      <Async q={q} rows={6}>
        {(d) => (
          <div className="space-y-4">
            {!d.llm.available && (
              <Notice tone="info" title="Running without a language model.">
                {d.llm.reason}. Analyses use a keyword heuristic and are marked low confidence until a model is configured.
              </Notice>
            )}
            <Panel title="Latest analysis per company">
              {d.items.length === 0 ? (
                <EmptyState title="No analyses yet">Pick a company above. Its last con-call transcripts are fetched from NSE, each promise is checked on the web, and follow-through is scored.</EmptyState>
              ) : (
                <div className="scroll-x">
                  <table>
                    <thead>
                      <tr>
                        <th>Company</th>
                        <th className="r">Score</th>
                        <th>Result</th>
                        <th>Quarters</th>
                        <th>Method</th>
                        <th>Run</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.items.map((r: any) => (
                        <tr key={r.instrument_id}>
                          <td>
                            <Link to={`/concalls/NSE:${encodeURIComponent(r.symbol)}`} className="font-semibold">
                              {r.symbol}
                            </Link>
                            <div className="text-xs text-ink-2">{r.name}</div>
                          </td>
                          <td className="r num text-lg font-semibold">{r.score == null ? <Missing reason="Too few checkable commitments" /> : Math.round(r.score)}</td>
                          <td>
                            <Badge tone={r.score == null ? 'neutral' : r.score >= 75 ? 'good' : r.score >= 45 ? 'warn' : 'bad'}>{r.label}</Badge>
                          </td>
                          <td>
                            {r.previous} → {r.current}
                          </td>
                          <td className="text-ink-2">
                            {r.mode === 'llm' ? 'Language model' : 'Heuristic'}, {r.confidence.toLowerCase()} confidence
                          </td>
                          <td className="text-ink-2">{fmtDate(r.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
            {d.pending.length > 0 && (
              <Panel title="Transcripts stored, not yet analysed">
                <ul className="m-0 list-none p-0">
                  {d.pending.map((p: any) => (
                    <li key={p.instrument_id} className="flex justify-between border-b border-rule px-4 py-2 last:border-0">
                      <Link to={`/concalls/NSE:${encodeURIComponent(p.symbol)}`}>{p.symbol}</Link>
                      <span className="text-ink-2">
                        {p.transcripts} transcript{p.transcripts === 1 ? '' : 's'}
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        )}
      </Async>
    </>
  )
}

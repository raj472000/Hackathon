import { useMutation } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, post, waitForJob } from '../api/client'
import { fmtDate, fmtDateTime, fmtNum, fmtSigned, fmtUnit, signClass } from '../lib/format'
import { Sparkline } from './Charts'
import { Badge, Button, cx, Missing, Notice } from './ui'

const EXAMPLES = [
  'How is the market today?',
  'Strongest stocks in the last 3 days',
  'Which stocks have the lowest volatility?',
  'Top gainers in Information Technology this month',
  'Compare HDFCBANK and ICICIBANK',
  'Banks with ROE above 15 and low debt',
]

const instrumentPath = (symbol: string) => `/instrument/NSE:${encodeURIComponent(symbol)}`

type Bench = {
  symbol: string
  close: number
  last_bar: string
  return_1w: number
  return_1m: number
  sparkline: number[]
} | null

export function AskPanel({ benchmark, benchmarkSymbol, examples }: { benchmark: Bench; benchmarkSymbol: string; examples?: string[] }) {
  const [q, setQ] = useState('')
  const [history, setHistory] = useState<any[]>([])
  const m = useMutation<any, ApiError, string>({
    mutationFn: (question) => post('/ask', { question }),
    onSuccess: (d) => setHistory((h) => [d, ...h].slice(0, 5)),
  })
  const submit = (e?: FormEvent, text?: string) => {
    e?.preventDefault()
    const question = (text ?? q).trim()
    if (question.length < 3 || m.isPending) return
    setQ(question)
    m.mutate(question)
  }
  const current = history[0]
  return (
    <section aria-labelledby="ask-title" className="overflow-hidden rounded-[14px] bg-rail text-rail-ink">
      <div className="rail-grid grid gap-6 px-5 py-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(260px,1fr)] lg:px-7 lg:py-7">
        <form onSubmit={submit} className="min-w-0">
          <h2 id="ask-title" className="font-display text-[26px] font-semibold leading-tight text-white lg:text-[30px]">
            Ask the market
          </h2>
          <p className="mt-1 max-w-[60ch] text-[14px] text-rail-ink">
            Ask in plain words. Prices come live from NSE and the broker MCP sources; when a source is down, the answer uses the last values stored in the database and says so.
          </p>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <label htmlFor="ask-input" className="sr-only">
              Your question
            </label>
            <input
              id="ask-input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              maxLength={500}
              placeholder="e.g. Which banks have the strongest 3-month momentum?"
              className="min-h-[46px] w-full rounded-[9px] border border-rail-2 bg-[#0b1530] px-4 text-[15px] text-white placeholder:text-rail-ink-2 focus-visible:border-accent"
            />
            <Button type="submit" variant="accent" busy={m.isPending} className="min-h-[46px] px-5 text-[15px]" data-testid="ask-submit">
              Ask
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2" aria-label="Example questions">
            {(examples ?? EXAMPLES).map((x) => (
              <button
                key={x}
                type="button"
                onClick={() => submit(undefined, x)}
                className="rounded-full border border-rail-2 px-3 py-1 text-[12.5px] text-rail-ink transition-colors hover:border-accent hover:text-white"
              >
                {x}
              </button>
            ))}
          </div>
        </form>
        <div className="flex min-w-0 flex-col justify-between rounded-[10px] border border-rail-2 bg-[#0b1530]/70 p-4">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[13px] text-rail-ink-2">{benchmark?.symbol ?? benchmarkSymbol}</span>
            {benchmark && <span className="text-[11.5px] text-rail-ink-2">Close {fmtDate(benchmark.last_bar)}</span>}
          </div>
          {benchmark ? (
            <>
              <div className="num mt-1 font-display text-[34px] font-semibold leading-none text-white">{fmtNum(benchmark.close)}</div>
              <div className="mt-2 flex gap-4 text-[13px]">
                <span className={benchmark.return_1w >= 0 ? 'text-[#5fd8bf]' : 'text-[#ff9c8a]'}>{fmtSigned(benchmark.return_1w)} 1 week</span>
                <span className={benchmark.return_1m >= 0 ? 'text-[#5fd8bf]' : 'text-[#ff9c8a]'}>{fmtSigned(benchmark.return_1m)} 1 month</span>
              </div>
              <div className="mt-3 [&_path]:stroke-accent">
                <Sparkline values={benchmark.sparkline} width={320} height={64} label={`${benchmark.symbol} closes over the last 60 sessions`} />
              </div>
            </>
          ) : (
            <p className="mt-2 text-[13px] text-rail-ink">No stored history for {benchmarkSymbol} yet. Ask about the market to fetch a live index quote.</p>
          )}
        </div>
      </div>
      {(m.isPending || m.error || current) && (
        <div className="border-t border-rail-2 bg-surface px-5 py-5 text-ink lg:px-7" aria-live="polite">
          {m.isPending && <AskSkeleton question={q} />}
          {m.error && !m.isPending && (
            <Notice tone="bad" title="The question could not be answered.">
              {m.error.message}
            </Notice>
          )}
          {current && !m.isPending && <AskResult d={current} onAskAgain={() => submit(undefined, current.question)} />}
          {history.length > 1 && !m.isPending && (
            <div className="mt-5 border-t border-rule pt-3">
              <p className="text-xs text-ink-2">Earlier questions</p>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {history.slice(1).map((h, i) => (
                  <button
                    key={i}
                    onClick={() => setHistory((all) => [h, ...all.filter((x) => x !== h)])}
                    className="rounded-full border border-rule px-3 py-1 text-[12.5px] text-ink-2 hover:border-ink-3 hover:text-ink"
                  >
                    {h.question}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function AskSkeleton({ question }: { question: string }) {
  return (
    <div role="status" aria-label="Answering">
      <p className="text-[13px] text-ink-2">Reading “{question}”, fetching live data and checking the database…</p>
      <div className="mt-3 space-y-2">
        <div className="skeleton h-5 w-[85%]" />
        <div className="skeleton h-5 w-[70%]" />
        <div className="skeleton h-16 w-full" />
      </div>
    </div>
  )
}

function SourceTag({ e }: { e: any }) {
  if (e.source === 'unavailable') return <Badge tone="bad">Not available</Badge>
  if (e.kind === 'movers_window' && e.live) return <Badge tone="good">Live · MCP + stored history</Badge>
  return e.live ? <Badge tone="good">Live · MCP</Badge> : <Badge tone="info">Stored · database</Badge>
}

function EvidenceCard({ e }: { e: any }) {
  const d = e.data ?? {}
  const rows: Array<[string, any, string?]> = []
  if (e.kind === 'quote') {
    rows.push(['Last price', d.last_price, 'price'], ['Day change', d.change_pct, 'signed'], ['High', d.high, 'price'], ['Low', d.low, 'price'])
  } else if (e.kind === 'metrics') {
    for (const [k, label, unit] of [
      ['return_1m', '1-month return', 'signed'],
      ['return_3m', '3-month return', 'signed'],
      ['rel_strength_6m', 'Relative strength 6M', 'pct'],
      ['volatility_252d', 'Volatility 1Y', 'pct'],
      ['pe', 'P/E', 'x'],
      ['roce', 'ROCE', 'pct'],
      ['overall_score', 'Research score', 'score'],
    ] as const)
      if (k in d) rows.push([label, d[k], unit])
  }
  return (
    <div className="rounded-[9px] border border-rule bg-surface-2 p-3" data-testid={`evidence-${e.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">
          <span className="mr-1.5 rounded bg-surface px-1.5 py-0.5 text-[11px] text-ink-2">{e.id}</span>
          {e.title}
        </span>
        <SourceTag e={e} />
      </div>
      {rows.length > 0 && (
        <dl className="m-0 mt-2 grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
          {rows.map(([k, v, u]) => (
            <div key={k} className="flex justify-between gap-2">
              <dt className="text-ink-2">{k}</dt>
              <dd className={cx('num m-0 whitespace-nowrap font-medium', u === 'signed' && signClass(v))}>{v == null ? <Missing /> : u === 'signed' ? fmtSigned(v) : fmtUnit(v, u)}</dd>
            </div>
          ))}
        </dl>
      )}
      {e.kind === 'rotation' && d.sectors && (
        <ul className="m-0 mt-2 list-none space-y-0.5 p-0 text-[13px]">
          {Object.entries(d.sectors).map(([k, v]: any) => (
            <li key={k} className="flex justify-between gap-2">
              <span>{k}</span>
              <span className="text-ink-2">{v.quadrant ?? '—'}</span>
            </li>
          ))}
        </ul>
      )}
      {e.kind === 'movers_window' && (
        <p className="mt-2 text-[13px] text-ink-2">
          {d.ranked} instruments ranked over {d.sessions} sessions. {d.method}.
        </p>
      )}
      {e.kind === 'movers' && d.best_1w && (
        <p className="mt-2 text-[13px] text-ink-2">Best 1 week: {d.best_1w.map((x: any) => `${x.symbol} ${fmtSigned(x.return_1w)}`).join(', ')}</p>
      )}
      <p className="mt-2 text-[11.5px] text-ink-2">
        {e.source}
        {e.as_of && <> · as of {fmtDate(e.as_of)}</>}
        {e.retrieved_at && <> · retrieved {fmtDateTime(e.retrieved_at)}</>}
      </p>
      {e.note && <p className="mt-1 text-[12px] text-warn">{e.note}</p>}
    </div>
  )
}

function RefreshNotice({ r, onAskAgain }: { r: any; onAskAgain: () => void }) {
  const [state, setState] = useState<'running' | 'done' | 'failed'>('running')
  useEffect(() => {
    if (!r?.started_job) return
    let live = true
    waitForJob(r.started_job)
      .then((j: any) => live && setState(j.status === 'succeeded' ? 'done' : 'failed'))
      .catch(() => live && setState('failed'))
    return () => {
      live = false
    }
  }, [r?.started_job])
  if (!r || r.state !== 'stale') return null
  return (
    <div className="mt-3" data-testid="ask-refresh">
      <Notice tone={state === 'failed' ? 'bad' : 'warn'} title={`Stored prices end ${fmtDate(r.stored_until)}.`}>
        {r.started_job
          ? state === 'running'
            ? 'Fetching the latest daily prices from the MCP sources in the background. This answer uses live quotes where a source answered.'
            : state === 'done'
              ? 'The latest daily prices are now stored.'
              : 'The background price refresh did not complete; see Data status.'
          : r.note}
        {state === 'done' && (
          <Button variant="quiet" className="ml-2" onClick={onAskAgain}>
            Ask again
          </Button>
        )}
      </Notice>
    </div>
  )
}

function MoversTable({ rows, sessions }: { rows: any[]; sessions: number }) {
  return (
    <div className="scroll-x mt-4 rounded-[9px] border border-rule" data-testid="ask-movers">
      <table>
        <caption className="sr-only">Change over the last {sessions} sessions, ranked</caption>
        <thead>
          <tr>
            <th>#</th>
            <th>Instrument</th>
            <th>Sector</th>
            <th className="r">Price (₹)</th>
            <th className="r">{sessions} {sessions === 1 ? 'session' : 'sessions'}</th>
            <th className="r">Day</th>
            <th className="r">Up sessions</th>
            <th>vs 20-day avg</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.instrument_id}>
              <td className="num text-ink-2">{i + 1}</td>
              <td>
                <Link to={instrumentPath(r.symbol)} className="font-semibold">
                  {r.symbol}
                </Link>
                <div className="text-xs text-ink-2">{r.name}</div>
              </td>
              <td className="text-ink-2">{r.sector}</td>
              <td className="r num">
                {fmtNum(r.price)}
                <div className="text-[11px] text-ink-2">{r.live ? `live · ${r.price_source === 'kite_mcp' ? 'Kite' : 'NSE'}` : `close ${fmtDate(r.price_date)}`}</div>
              </td>
              <td className={`r num font-medium ${signClass(r.change_pct)}`}>{fmtSigned(r.change_pct)}</td>
              <td className={`r num ${signClass(r.day_change_pct)}`}>{r.day_change_pct == null ? <Missing /> : fmtSigned(r.day_change_pct)}</td>
              <td className="r num">
                {r.up_sessions} of {r.sessions}
              </td>
              <td className="text-ink-2">{r.above_sma20 == null ? <Missing /> : r.above_sma20 ? 'Above' : 'Below'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AskResult({ d, onAskAgain }: { d: any; onAskAgain: () => void }) {
  const sortKey = d.plan.sort_metric as string | null
  return (
    <div data-testid="ask-result">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[13px] text-ink-2">
          You asked: <span className="font-medium text-ink">{d.question}</span>
        </p>
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={d.plan_source === 'llm' ? 'good' : 'neutral'} title="Who interpreted the question">
            Understood by {d.plan_source === 'llm' ? 'the language model' : 'rules'}
          </Badge>
          <Badge tone={d.answer_source === 'llm' ? 'good' : 'neutral'} title="Who wrote the answer">
            Answer by {d.answer_source === 'llm' ? `the language model` : 'rules'}
          </Badge>
          <Badge tone="info">
            {d.data_sources.live_mcp} live · {d.data_sources.system_db} stored
          </Badge>
        </div>
      </div>
      <p className="mt-3 max-w-[80ch] text-[16px] leading-relaxed" data-testid="ask-answer">
        {d.answer.answer}
      </p>
      {d.llm_disclaimer && (
        <p className="mt-2 max-w-[80ch] border-l-2 border-accent pl-3 text-[12.5px] text-ink-2" data-testid="ask-llm-disclaimer">
          {d.llm_disclaimer}
        </p>
      )}
      <RefreshNotice r={d.data_refresh} onAskAgain={onAskAgain} />
      {d.answer_source === 'llm' && d.answer.points.length > 0 && (
        <ul className="mt-2 max-w-[80ch] list-disc space-y-1 pl-5 text-[14px]">
          {d.answer.points.map((p: any, i: number) => (
            <li key={i}>
              {p.text} <span className="text-[11.5px] text-ink-2">[{p.evidence_ids.join(', ')}]</span>
            </li>
          ))}
        </ul>
      )}
      {d.llm.reason_not_used && <p className="mt-2 text-[12.5px] text-ink-2">The language model was not used: {d.llm.reason_not_used}</p>}
      {(d.plan.filters_described.length > 0 || d.plan.sectors.length > 0 || d.plan_notes.length > 0) && (
        <div className="mt-3 flex flex-wrap gap-1.5 text-[12.5px]">
          {d.plan.sectors.map((s: string) => (
            <Badge key={s} tone="accent">
              {s}
            </Badge>
          ))}
          {d.plan.filters_described.map((f: string) => (
            <Badge key={f}>{f}</Badge>
          ))}
          {d.plan_notes.map((n: string) => (
            <span key={n} className="text-warn">
              {n}
            </span>
          ))}
        </div>
      )}
      {d.movers?.length > 0 && <MoversTable rows={d.movers} sessions={d.plan.lookback_days ?? d.movers[0].sessions} />}
      {d.table.length > 0 && (
        <div className="scroll-x mt-4 rounded-[9px] border border-rule">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Instrument</th>
                <th>Sector</th>
                <th className="r">Close (₹)</th>
                {sortKey && sortKey !== 'close' && <th className="r">{sortKey.replace(/_/g, ' ')}</th>}
                <th className="r">3 months</th>
                <th>Risk</th>
              </tr>
            </thead>
            <tbody>
              {d.table.map((r: any, i: number) => (
                <tr key={r.instrument_id}>
                  <td className="num text-ink-2">{i + 1}</td>
                  <td>
                    <Link to={instrumentPath(r.symbol)} className="font-semibold">
                      {r.symbol}
                    </Link>
                    <div className="text-xs text-ink-2">{r.name}</div>
                  </td>
                  <td className="text-ink-2">{r.sector}</td>
                  <td className="r num">{fmtNum(r.values.close)}</td>
                  {sortKey && sortKey !== 'close' && <td className="r num font-medium">{fmtNum(r.values[sortKey])}</td>}
                  <td className={`r num ${signClass(r.values.return_3m)}`}>{fmtSigned(r.values.return_3m)}</td>
                  <td className="text-ink-2">{r.risk_label ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d.evidence.length > 0 && (
        <div className="mt-4">
          <p className="text-[12.5px] font-medium text-ink-2">Evidence used</p>
          <div className="mt-2 grid gap-3 lg:grid-cols-2">
            {d.evidence
              .filter((e: any) => e.kind !== 'screen')
              .map((e: any) => (
                <EvidenceCard key={e.id} e={e} />
              ))}
          </div>
        </div>
      )}
      <p className="mt-4 text-[11.5px] text-ink-3">{d.disclaimer}</p>
    </div>
  )
}

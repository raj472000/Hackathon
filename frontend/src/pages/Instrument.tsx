import { Fragment, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useApi, post } from '../api/client'
import { PriceChart } from '../components/Charts'
import { EntrySetup, ScoreBreakdown } from '../components/Score'
import { Async, Badge, EmptyState, Kv, Missing, Notice, Panel, RefreshButton, StateBadge, Tabs } from '../components/ui'
import { PROVIDER_LABEL, STATUS_LABEL, ago, fmtDate, fmtDateTime, fmtMetric, fmtNum, fmtSigned, signClass } from '../lib/format'
import { instrumentPath } from './Overview'

export function QuoteHeader({ d }: { d: any }) {
  const ref = `${d.instrument.exchange}:${d.instrument.symbol}`
  // The page renders from stored data at once; the live quote is fetched separately and may take a few seconds.
  const live = useApi<any>(['quote', ref], d.quote_deferred ? `/instruments/${encodeURIComponent(ref)}/quote` : null, undefined, {
    staleTime: 30_000,
    refetchInterval: (query) => (query.state.data?.quote?.status === 'pending' ? 4_000 : false),
  })
  const quote = d.quote_deferred ? live.data?.quote : d.quote
  const quoteLoading = d.quote_deferred && (live.isLoading || quote?.status === 'pending')
  const f = quote?.fields ?? {}
  const lp = f.last_price
  const prev = f.prev_close?.value
  const chg = lp?.value != null && prev ? (lp.value / prev - 1) * 100 : null
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-4 rounded-lg border border-rule bg-surface px-4 py-4">
      <div>
        <div className="text-[13px] text-ink-2">
          {d.instrument.exchange} · {d.instrument.instrument_type === 'INDEX' ? 'Index' : (d.instrument.sector ?? 'Equity')}
          {d.instrument.industry && ` · ${d.instrument.industry}`}
        </div>
        <h1 className="text-[26px] font-semibold leading-tight">
          {d.instrument.symbol} <span className="font-normal text-ink-2">{d.instrument.name}</span>
        </h1>
      </div>
      <div className="flex flex-col items-end gap-2 text-right">
        <RefreshButton
          keys={[['instrument'], ['quote'], ['instrument-metrics'], ['history'], ['financials'], ['research']]}
          job={() => post(`/instruments/${encodeURIComponent(`${d.instrument.exchange}:${d.instrument.symbol}`)}/refresh`)}
          label="Fetch latest"
          hint="Fetch the latest prices for this instrument from the MCP sources and recompute its metrics"
        />
        <div>
        {quoteLoading ? (
          <div role="status" aria-live="polite" className="flex flex-col items-end gap-1.5">
            <div className="skeleton h-8 w-40" />
            <span className="text-xs text-ink-2">{quote?.reason ?? 'Fetching the live quote from NSE and Kite…'}</span>
          </div>
        ) : lp?.value != null ? (
          <>
            <div className="num font-display text-[34px] font-semibold leading-none">
              <span className="mr-1 text-lg text-ink-2">₹</span>
              {fmtNum(lp.value)}
            </div>
            <div className={`num mt-1 ${signClass(chg)}`}>{fmtSigned(chg, 2)} vs previous close</div>
            <div className="mt-1 flex flex-wrap justify-end gap-1.5 text-xs text-ink-2">
              <Badge tone={lp.status === 'delayed' ? 'warn' : 'neutral'}>{lp.status === 'delayed' ? 'Delayed quote' : 'Quote'}</Badge>
              <span>
                {PROVIDER_LABEL[lp.provider] ?? lp.provider}, as of {fmtDateTime(lp.as_of)}
              </span>
              {lp.conflict && <Badge tone="bad">Sources disagree by {fmtNum(lp.conflict.diff_pct, 2)}%</Badge>}
            </div>
          </>
        ) : (
          <div>
            <div className="text-lg">
              <Missing reason="No source returned a quote" status="unavailable" /> No quote
            </div>
            <ul className="m-0 mt-1 list-none p-0 text-xs text-ink-2">
              {(quote?.sources ?? []).map((s: any) => (
                <li key={s.provider}>
                  {PROVIDER_LABEL[s.provider] ?? s.provider}: {STATUS_LABEL[s.status] ?? s.status}
                </li>
              ))}
            </ul>
          </div>
        )}
        </div>
      </div>
    </div>
  )
}

const GROUP_TITLE: Record<string, string> = { fundamentals: 'Fundamentals and quality', valuation: 'Valuation', price: 'Price action and technicals', risk: 'Risk and liquidity', context: 'Sector context', other: 'Other' }

function MetricsTab({ refId }: { refId: string }) {
  const q = useApi<any>(['instrument-metrics', refId], `/instruments/${refId}/metrics`)
  const [open, setOpen] = useState<string | null>(null)
  return (
    <Async q={q} rows={12}>
      {(d) =>
        d.data_state === 'missing' ? (
          <EmptyState title="No metrics computed">{d.data_state_reason}</EmptyState>
        ) : (
          <div>
            <p className="m-0 px-4 pt-3 text-[13px] text-ink-2">
              {d.coverage.available} of {d.coverage.total} metrics available, computed {fmtDateTime(d.computed_at)} from data as of {fmtDate(d.as_of)}. All values here are calculated; select a row to see its formula and inputs.
            </p>
            {Object.keys(GROUP_TITLE).map((g) => {
              const rows = d.metrics.filter((m: any) => m.group === g)
              if (rows.length === 0 || g === 'other') return null
              return (
                <div key={g} className="mt-3">
                  <h3 className="px-4 text-[14px] font-semibold">{GROUP_TITLE[g]}</h3>
                  <div className="scroll-x">
                    <table className="text-[13px]">
                      <tbody>
                        {rows.map((m: any) => (
                          <Fragment key={m.name}>
                            <tr className="cursor-pointer" onClick={() => setOpen(open === m.name ? null : m.name)}>
                              <td>
                                <button className="text-left font-medium" aria-expanded={open === m.name}>
                                  {m.label}
                                </button>
                              </td>
                              <td className="r num w-40">{m.value == null ? <Missing status={m.status} reason={m.reason} /> : fmtMetric(m.name, m.value, m.unit)}</td>
                              <td className="w-56 text-ink-2">{m.value == null ? `${STATUS_LABEL[m.status] ?? m.status}` : ''}</td>
                            </tr>
                            {open === m.name && (
                              <tr>
                                <td colSpan={3} className="bg-surface-2 text-[13px]">
                                  {m.reason && <div className="mb-1">Reason: {m.reason}</div>}
                                  <div>
                                    <span className="text-ink-2">Formula: </span>
                                    {m.formula || 'not recorded'}
                                  </div>
                                  {m.inputs && Object.keys(m.inputs).length > 0 && (
                                    <div className="num">
                                      <span className="text-ink-2">Inputs: </span>
                                      {Object.entries(m.inputs).map(([k, v]) => `${k.replace(/_/g, ' ')} = ${typeof v === 'number' ? fmtNum(v) : (v ?? 'missing')}`).join('; ')}
                                    </div>
                                  )}
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )
            })}
          </div>
        )
      }
    </Async>
  )
}

const FIN_ROWS: Array<[string, string]> = [
  ['revenue', 'Revenue'], ['ebitda', 'EBITDA'], ['ebit', 'EBIT'], ['interest', 'Finance costs'], ['pbt', 'Profit before tax'], ['tax', 'Tax'], ['pat', 'Profit after tax'], ['eps', 'EPS (₹)'],
  ['equity', "Shareholders' equity"], ['total_debt', 'Total debt'], ['cash', 'Cash and equivalents'], ['total_assets', 'Total assets'], ['current_assets', 'Current assets'], ['current_liabilities', 'Current liabilities'],
  ['cfo', 'Cash flow from operations'], ['capex', 'Capital expenditure'], ['dividends_paid', 'Dividends paid'], ['shares_outstanding', 'Shares outstanding (crore)'],
]

function FinancialsTab({ refId }: { refId: string }) {
  const [pt, setPt] = useState('annual')
  const q = useApi<any>(['financials', refId, pt], `/instruments/${refId}/financials`, { period_type: pt })
  return (
    <div>
      <div className="flex gap-1 px-4 pt-3" role="group" aria-label="Period type">
        {['annual', 'quarterly'].map((p) => (
          <button key={p} aria-pressed={pt === p} onClick={() => setPt(p)} className={`rounded border px-2 py-1 text-xs ${pt === p ? 'border-accent bg-accent-soft font-semibold' : 'border-rule-strong'}`}>
            {p === 'annual' ? 'Annual' : 'Quarterly'}
          </button>
        ))}
      </div>
      <Async q={q} rows={10}>
        {(d) =>
          d.periods.length === 0 ? (
            <EmptyState title={`No ${pt} statements`}>{d.data_state_reason}</EmptyState>
          ) : (
            <>
              <p className="m-0 px-4 pt-3 text-[13px] text-ink-2">
                Reported values in {d.currency} crore (reported in {d.reported_unit ?? d.unit}). Source: {d.source}. Ingested {fmtDateTime(d.ingested_at)}.{d.is_demo && ' Synthetic demo figures.'}
              </p>
              <div className="scroll-x mt-2">
                <table className="text-[13px]">
                  <thead>
                    <tr>
                      <th>Period ending</th>
                      {d.periods.map((p: any) => (
                        <th key={p.period_end} className="r">
                          {fmtDate(p.period_end)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {FIN_ROWS.map(([k, label]) => (
                      <tr key={k}>
                        <td className="font-medium">{label}</td>
                        {d.periods.map((p: any) => (
                          <td key={p.period_end} className="r num">
                            {p[k] == null ? <Missing reason="Not reported in the source file" /> : fmtNum(p[k], k === 'eps' ? 2 : 0)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </Async>
    </div>
  )
}

function PriceTab({ refId }: { refId: string }) {
  const q = useApi<any>(['history', refId], `/instruments/${refId}/history`, { limit: 800 })
  return (
    <Async q={q} rows={10}>
      {(d) =>
        d.bars.length === 0 ? (
          <EmptyState title="No price history">{d.data_state_reason}</EmptyState>
        ) : (
          <div>
            <PriceChart bars={d.bars} />
            <div className="grid gap-4 px-4 py-4 lg:grid-cols-2">
              <div>
                <h3 className="mb-1 text-[14px] font-semibold">Where these prices come from</h3>
                <dl className="m-0">
                  <Kv k="Bars shown">{d.coverage.bars}</Kv>
                  {Object.entries(d.coverage.by_provider).map(([p, n]) => (
                    <Kv key={p} k={`From ${PROVIDER_LABEL[p] ?? p}`}>
                      {n as number}
                    </Kv>
                  ))}
                  <Kv k="Range">
                    {fmtDate(d.coverage.first)} to {fmtDate(d.coverage.last)}
                  </Kv>
                  <Kv k="Freshness">
                    <StateBadge state={d.freshness} label={d.freshness === 'stale' ? `Stale, ${d.data_age_days} days old` : 'Fresh'} />
                  </Kv>
                  <Kv k="Adjustment">{d.adjustment}</Kv>
                </dl>
                {d.corporate_actions.length > 0 && (
                  <>
                    <h3 className="mb-1 mt-4 text-[14px] font-semibold">Corporate actions applied</h3>
                    <ul className="m-0 list-none space-y-1 p-0 text-[13px]">
                      {d.corporate_actions.map((a: any) => (
                        <li key={a.ex_date + a.description}>
                          <span className="font-medium">{fmtDate(a.ex_date)}</span> {a.kind}
                          {a.ratio && a.kind !== 'dividend' ? ` (ratio ${fmtNum(a.ratio)})` : ''} <span className="text-ink-2">{a.description}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
              <div>
                <h3 className="mb-1 text-[14px] font-semibold">Source disagreements</h3>
                {d.conflicts.length === 0 ? (
                  <p className="m-0 text-[13px] text-ink-2">None recorded. Where NSE and the broker both report a bar, their values agree within tolerance.</p>
                ) : (
                  <div className="scroll-x">
                    <table className="text-[13px]">
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Field</th>
                          <th className="r">NSE</th>
                          <th className="r">Broker</th>
                          <th className="r">Gap</th>
                          <th>Used</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.conflicts.map((c: any) => (
                          <tr key={c.date + c.field}>
                            <td>{fmtDate(c.date)}</td>
                            <td>{c.field}</td>
                            <td className="r num">{fmtNum(c.values.nse_mcp)}</td>
                            <td className="r num">{fmtNum(c.values.kite_mcp)}</td>
                            <td className="r num text-loss">{fmtNum(c.diff_pct, 2)}%</td>
                            <td>{PROVIDER_LABEL[c.chosen] ?? c.chosen}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <h3 className="mb-1 mt-4 text-[14px] font-semibold">Latest source calls</h3>
                <ul className="m-0 list-none space-y-1 p-0 text-[13px]">
                  {d.lineage.slice(0, 6).map((l: any, i: number) => (
                    <li key={i} className="flex flex-wrap items-center gap-2">
                      <StateBadge state={l.status} />
                      <span className="font-medium">{PROVIDER_LABEL[l.provider] ?? l.provider}</span>
                      <span className="text-ink-2">
                        {l.dataset.replace(/_/g, ' ')}
                        {l.rows != null ? `, ${l.rows} rows` : ''}
                        {l.source_id ? ` via ${l.source_id}` : ''} · {ago(l.retrieved_at)}
                      </span>
                      {l.reason && <span className="basis-full text-xs text-ink-2">{l.reason}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        )
      }
    </Async>
  )
}

export function Instrument() {
  const { ref = '' } = useParams()
  const refId = encodeURIComponent(ref)
  const [tab, setTab] = useState('summary')
  const q = useApi<any>(['instrument', ref], `/instruments/${refId}`, { quote: false })
  return (
    <Async q={q} rows={12}>
      {(d) => {
        const isIndex = d.instrument.instrument_type === 'INDEX'
        const sc = d.snapshot?.score
        return (
          <>
            <QuoteHeader d={d} />
            {!isIndex && (
              <div className="mb-3 flex flex-wrap gap-3 text-[13px]">
                <Link to={`/research/${ref}`}>Open research board</Link>
                <Link to={`/concalls/${ref}`}>Concall follow-through</Link>
                <Link to="/watchlists">Add to a watchlist</Link>
              </div>
            )}
            <Tabs
              label="Instrument sections"
              value={tab}
              onChange={setTab}
              tabs={[{ id: 'summary', label: 'Summary' }, { id: 'price', label: 'Price and provenance' }, ...(isIndex ? [] : [{ id: 'financials', label: 'Financial statements' }, { id: 'metrics', label: 'All metrics' }])]}
            />
            <div className="mt-3 space-y-4">
              {tab === 'summary' &&
                (d.snapshot == null ? (
                  <Panel>
                    <EmptyState title={isIndex ? 'Indices are not scored' : 'No metrics yet'}>{isIndex ? 'Scores, themes and entry setup apply to tradable instruments. See the price tab for history.' : d.data_state_reason}</EmptyState>
                  </Panel>
                ) : (
                  <>
                    {sc.freshness === 'stale' && <Notice tone="warn" title="Stale prices.">The latest price bar is {fmtDate(sc.last_bar)} ({sc.data_age_days} days old). Scores below are computed on that date's data.</Notice>}
                    <Panel title="Research score" aside={<span>Metrics as of {fmtDate(d.snapshot.as_of)}</span>}>
                      <ScoreBreakdown score={sc} versions={d.snapshot.calculation_version} asOf={fmtDate(d.snapshot.as_of)} />
                    </Panel>
                    <div className="grid gap-4 lg:grid-cols-2">
                      <Panel title="Entry setup">
                        <EntrySetup setup={sc.entry_setup} />
                      </Panel>
                      <div className="space-y-4">
                        <Panel title="Theme fit" aside={<Link to="/themes">All themes</Link>}>
                          {d.themes.length === 0 ? (
                            <EmptyState title="Not in any theme's latest snapshot" />
                          ) : (
                            <ul className="m-0 max-h-72 list-none overflow-auto p-0">
                              {d.themes.map((t: any) => (
                                <li key={t.theme_id} className="flex items-baseline justify-between gap-3 border-b border-rule px-4 py-2 last:border-0">
                                  <span>
                                    <Link to={`/themes/${t.theme_id}`}>{t.name}</Link> <span className="text-xs text-ink-2">{t.category}</span>
                                  </span>
                                  <span className="num whitespace-nowrap text-ink-2">
                                    rank {t.rank}, {t.period}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </Panel>
                        <Panel title={`Sector peers: ${d.instrument.sector ?? ''}`}>
                          <div className="scroll-x">
                            <table className="text-[13px]">
                              <thead>
                                <tr>
                                  <th>Symbol</th>
                                  <th className="r">Score</th>
                                  <th className="r">3M</th>
                                  <th className="r">P/E</th>
                                  <th className="r">ROE</th>
                                </tr>
                              </thead>
                              <tbody>
                                {d.peers.map((p: any) => (
                                  <tr key={p.instrument_id} className={p.symbol === d.instrument.symbol ? 'bg-accent-soft' : ''}>
                                    <td>
                                      <Link to={instrumentPath(p.symbol)} className="font-medium">
                                        {p.symbol}
                                      </Link>
                                    </td>
                                    <td className="r num">{p.overall_score == null ? <Missing reason="Incomplete score" /> : Math.round(p.overall_score)}</td>
                                    <td className={`r num ${signClass(p.return_3m)}`}>{fmtSigned(p.return_3m)}</td>
                                    <td className="r num">{p.pe == null ? <Missing /> : `${fmtNum(p.pe, 1)}x`}</td>
                                    <td className="r num">{p.roe == null ? <Missing /> : `${fmtNum(p.roe, 1)}%`}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </Panel>
                      </div>
                    </div>
                  </>
                ))}
              {tab === 'price' && (
                <Panel title="Daily prices">
                  <PriceTab refId={refId} />
                </Panel>
              )}
              {tab === 'financials' && (
                <Panel title="Financial statements">
                  <FinancialsTab refId={refId} />
                </Panel>
              )}
              {tab === 'metrics' && (
                <Panel title="All computed metrics">
                  <MetricsTab refId={refId} />
                </Panel>
              )}
            </div>
          </>
        )
      }}
    </Async>
  )
}

import { Link } from 'react-router-dom'
import { api, useApi } from '../api/client'
import { AskPanel } from '../components/AskPanel'
import { Async, EmptyState, Missing, PageHeader, Panel, RefreshButton, StateBadge } from '../components/ui'
import { ago, fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'

export const instrumentPath = (symbol: string, exchange = 'NSE') => `/instrument/${exchange}:${encodeURIComponent(symbol)}`

function Movers({ title, rows, unit }: { title: string; rows: any[]; unit: 'pct' | 'score' }) {
  return (
    <Panel title={title}>
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.instrument_id}>
              <td>
                <Link to={instrumentPath(r.symbol)} className="font-semibold">
                  {r.symbol}
                </Link>
                <div className="text-xs text-ink-2">{r.sector}</div>
              </td>
              <td className={`r num font-medium ${unit === 'pct' ? signClass(r.value) : ''}`}>{unit === 'pct' ? fmtSigned(r.value) : fmtNum(r.value, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

export function Overview() {
  const q = useApi<any>(['overview'], '/overview')
  const wl = useApi<any>(['watchlists'], '/watchlists')
  const watched = (wl.data?.items ?? []).flatMap((w: any) => w.items.map((i: any) => ({ ...i, list: w.name }))).slice(0, 6)
  return (
    <>
      <PageHeader
        title="Overview"
        lede="Ask a question, then see where the tracked universe stands, which sectors are rotating and how fresh the data is."
        refresh={<RefreshButton keys={[['overview'], ['watchlists'], ['sources']]} action={() => api('/overview', { params: { fresh: true } })} hint="Re-check the live NSE sources and reload this page" />}
      />
      <Async q={q} rows={10}>
        {(d) => (
          <div className="space-y-5">
            <AskPanel benchmark={d.benchmark} benchmarkSymbol={d.benchmark_symbol} />
            {d.data_state === 'missing' ? (
            <Panel>
              <EmptyState title="No market data stored yet" action={<Link to="/status">Open Data status to start a refresh</Link>}>
                {d.data_state_reason} Questions above still use the live MCP sources.
              </EmptyState>
            </Panel>
          ) : (
            <div className="space-y-5">
              <div className="grid gap-5">
                <Panel title="Universe" aside={<span>Metrics as of {fmtDate(d.as_of)}</span>}>
                  <dl className="m-0 grid grid-cols-2 gap-x-6 px-4 py-3 md:grid-cols-3 xl:grid-cols-6">
                    {[
                      ['Instruments scored', d.universe.instruments],
                      ['Complete five-pillar scores', d.universe.complete_scores],
                      ['Up over 1 week', d.universe.breadth?.advancing_1w],
                      ['Down over 1 week', d.universe.breadth?.declining_1w],
                      ['Stale price data', d.universe.stale],
                    ].map(([k, v]) => (
                      <div key={k as string} className="border-b border-rule py-2">
                        <dt className="text-xs text-ink-2">{k}</dt>
                        <dd className="num m-0 text-lg font-semibold">{v == null ? <Missing /> : v}</dd>
                      </div>
                    ))}
                    <div className="border-b border-rule py-2">
                      <dt className="text-xs text-ink-2">Exchange breadth (NSE MCP)</dt>
                      <dd className="num m-0 text-[13px]">
                        {d.market_breadth.state === 'ok' ? (
                          `${d.market_breadth.data?.advances ?? '—'} up, ${d.market_breadth.data?.declines ?? '—'} down`
                        ) : d.market_breadth.state === 'checking' ? (
                          <span className="text-ink-2" title={d.market_breadth.reason}>
                            Checking…
                          </span>
                        ) : (
                          <Missing reason={d.market_breadth.reason} status={d.market_breadth.state} />
                        )}
                      </dd>
                    </div>
                  </dl>
                </Panel>
              </div>

              <Panel title="Sector performance" aside={d.rotation ? <span>Rotation computed {ago(d.rotation.computed_at)}, data to {fmtDate(d.rotation.data_as_of)}</span> : undefined}>
                {d.sectors.length === 0 ? (
                  <EmptyState title="No sector rotation snapshot yet" action={<Link to="/rotation">Open sector rotation</Link>} />
                ) : (
                  <div className="scroll-x">
                    <table>
                      <thead>
                        <tr>
                          <th>Sector</th>
                          <th>Rotation quadrant</th>
                          <th className="r">1 month</th>
                          <th className="r">3 months</th>
                          <th>Series</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.sectors.map((s: any) => (
                          <tr key={s.sector}>
                            <td className="font-medium">{s.sector}</td>
                            <td>{s.quadrant ?? <Missing status={s.status} />}</td>
                            <td className={`r num ${signClass(s.return_1m)}`}>{fmtSigned(s.return_1m)}</td>
                            <td className={`r num ${signClass(s.return_3m)}`}>{fmtSigned(s.return_3m)}</td>
                            <td className="text-ink-2">{s.series_kind === 'composite' ? 'Composite of constituents' : s.series_kind === 'index' ? 'Sector index' : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>

              <div className="grid gap-4 md:grid-cols-3">
                <Movers title="Highest research scores" rows={d.top_scores} unit="score" />
                <Movers title="Strongest 3-month return" rows={d.top_momentum} unit="pct" />
                <Movers title="Weakest 3-month return" rows={d.weakest_momentum} unit="pct" />
              </div>

              <Panel title="Watchlist highlights" aside={<Link to="/watchlists">Watchlists and alerts</Link>}>
                {watched.length === 0 ? (
                  <EmptyState title="Nothing on a watchlist yet" action={<Link to="/watchlists">Create a watchlist</Link>} />
                ) : (
                  <div className="scroll-x">
                    <table>
                      <thead>
                        <tr>
                          <th>Instrument</th>
                          <th>List</th>
                          <th className="r">Close (₹)</th>
                          <th className="r">1 week</th>
                          <th>Entry setup</th>
                        </tr>
                      </thead>
                      <tbody>
                        {watched.map((i: any) => (
                          <tr key={`${i.list}-${i.item_id}`}>
                            <td>
                              <Link to={instrumentPath(i.symbol)} className="font-semibold">
                                {i.symbol}
                              </Link>
                            </td>
                            <td className="text-ink-2">{i.list}</td>
                            <td className="r num">{i.close == null ? <Missing reason="No price history ingested" /> : fmtNum(i.close)}</td>
                            <td className={`r num ${signClass(i.return_1w)}`}>{fmtSigned(i.return_1w)}</td>
                            <td>{i.entry_state ? <StateBadge state={i.entry_state} label={i.entry_state} /> : <Missing />}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>

              <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Theme leaders" aside={<Link to="/themes">All themes</Link>}>
                  {d.theme_leaders.length === 0 ? (
                    <EmptyState title="No theme snapshots yet">Themes are ranked monthly after data is ingested.</EmptyState>
                  ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>Theme</th>
                          <th>Ranked first</th>
                          <th>Snapshot</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.theme_leaders.map((t: any) => (
                          <tr key={t.theme_id}>
                            <td>
                              <Link to={`/themes/${t.theme_id}`}>{t.theme}</Link>
                            </td>
                            <td>
                              <Link to={instrumentPath(t.symbol)} className="font-semibold">
                                {t.symbol}
                              </Link>
                            </td>
                            <td className="text-ink-2">{t.period}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Panel>
                <Panel title="Recent refresh jobs" aside={<Link to="/status">Data status</Link>}>
                  <table>
                    <tbody>
                      {d.jobs.map((j: any) => (
                        <tr key={j.job_id}>
                          <td className="font-medium">{j.kind.replace(/_/g, ' ')}</td>
                          <td>
                            <StateBadge state={j.status} />
                          </td>
                          <td className="text-ink-2" title={fmtDateTime(j.started_at)}>
                            {ago(j.started_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Panel>
              </div>
            </div>
          )}
          </div>
        )}
      </Async>
    </>
  )
}

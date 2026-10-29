import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, useApi } from '../api/client'
import { AskPanel } from '../components/AskPanel'
import { MarketDesk } from '../components/MarketDesk'
import { Async, DivergingBar, EmptyState, MagnitudeBar, Missing, Panel, QuadrantChip, RefreshButton, StatTile, StateBadge } from '../components/ui'
import { ago, fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'

export const instrumentPath = (symbol: string, exchange = 'NSE') => `/instrument/${exchange}:${encodeURIComponent(symbol)}`

function Movers({ title, rows, unit, aside }: { title: string; rows: any[]; unit: 'pct' | 'score'; aside?: string }) {
  const max = Math.max(0, ...rows.map((r) => Math.abs(r.value ?? 0)))
  const tone = unit === 'score' ? 'link' : undefined
  return (
    <Panel title={title} aside={aside ? <span>{aside}</span> : undefined}>
      {rows.length === 0 ? (
        <EmptyState title="Nothing ranked yet" />
      ) : (
        <ol className="m-0 list-none p-0">
          {rows.map((r, i) => (
            <li key={r.instrument_id} className="flex items-center gap-3 border-b border-rule px-4 py-2.5 last:border-0">
              <span className="num grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full bg-surface-2 text-[11.5px] font-semibold text-ink-2" aria-label={`Rank ${i + 1}`}>
                {i + 1}
              </span>
              <span className="min-w-0 flex-1">
                <Link to={instrumentPath(r.symbol)} className="block truncate font-semibold no-underline hover:underline">
                  {r.symbol}
                </Link>
                <span className="block truncate text-xs text-ink-2">{r.sector ?? r.name ?? 'Sector not classified'}</span>
              </span>
              <span className="flex flex-col items-end gap-1.5">
                <span className={`num font-semibold ${unit === 'pct' ? signClass(r.value) : ''}`}>{unit === 'pct' ? fmtSigned(r.value) : fmtNum(r.value, 0)}</span>
                {r.value != null && <MagnitudeBar value={r.value} max={max} tone={tone ?? (r.value >= 0 ? 'gain' : 'loss')} />}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}

const SECTOR_PREVIEW = 8

export function Overview() {
  const [allSectors, setAllSectors] = useState(false)
  const q = useApi<any>(['overview'], '/overview')
  const wl = useApi<any>(['watchlists'], '/watchlists')
  const watched = (wl.data?.items ?? []).flatMap((w: any) => w.items.map((i: any) => ({ ...i, list: w.name }))).slice(0, 6)
  return (
    <>
      <div className="mb-6">
        <MarketDesk actions={<RefreshButton keys={[['overview'], ['watchlists'], ['sources'], ['indices'], ['nifty50-ticker']]} action={() => api('/overview', { params: { fresh: true } })} hint="Re-check the live NSE sources and reload this page" />} />
      </div>
      <Async q={q} rows={10}>
        {(d) => {
          const sectorMax = Math.max(0, ...d.sectors.map((s: any) => Math.abs(s.return_1m ?? 0)))
          return (
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
              <section aria-labelledby="universe-h">
                <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id="universe-h" className="text-[15.5px] font-semibold">
                    Universe
                  </h2>
                  <span className="text-xs text-ink-2">Metrics as of {fmtDate(d.as_of)}</span>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <StatTile
                    icon="screener"
                    label="Instruments scored"
                    value={d.universe.instruments == null ? <Missing /> : fmtNum(d.universe.instruments, 0)}
                    foot={d.universe.complete_scores == null ? undefined : `${fmtNum(d.universe.complete_scores, 0)} with complete five-pillar scores`}
                  />
                  {(() => {
                    const up = d.universe.breadth?.advancing_1w
                    const down = d.universe.breadth?.declining_1w
                    const ok = up != null && down != null && up + down > 0
                    return (
                      <StatTile
                        icon="flows"
                        label="Breadth over 1 week"
                        value={ok ? `${fmtNum((up / (up + down)) * 100, 0)}% up` : <Missing reason="Weekly returns not computed" />}
                        meter={ok ? { up, down } : null}
                        foot={ok ? `${fmtNum(up, 0)} up · ${fmtNum(down, 0)} down (stored history)` : undefined}
                      />
                    )
                  })()}
                  {(() => {
                    const mb = d.market_breadth
                    const a = mb.data?.advances
                    const dn = mb.data?.declines
                    return (
                      <StatTile
                        icon="status"
                        label={mb.provider === 'kite_mcp' ? 'Exchange breadth (Zerodha Kite)' : 'Exchange breadth (NSE MCP)'}
                        tone={mb.state === 'ok' ? 'info' : 'warn'}
                        value={
                          mb.state === 'ok' && a != null && dn != null ? (
                            `${fmtNum(a, 0)} / ${fmtNum(dn, 0)}`
                          ) : mb.state === 'checking' ? (
                            <span className="text-base text-ink-2" title={mb.reason}>
                              Checking…
                            </span>
                          ) : (
                            <Missing reason={mb.reason} status={mb.state} />
                          )
                        }
                        meter={mb.state === 'ok' && a != null && dn != null ? { up: a, down: dn } : null}
                        foot={
                          mb.state !== 'ok'
                            ? (mb.reason ?? 'Live source not reachable')
                            : mb.provider === 'kite_mcp'
                              ? `Advances / declines today, from Kite quotes on ${fmtNum(mb.data?.priced, 0)} stocks (NSE unavailable)`
                              : 'Advances / declines today'
                        }
                      />
                    )
                  })()}
                  <StatTile
                    icon="alerts"
                    label="Stale price data"
                    tone={d.universe.stale ? 'warn' : 'good'}
                    value={d.universe.stale ?? <Missing />}
                    foot="Price data more than 4 days old"
                  />
                </div>
              </section>

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
                          <th className="hidden w-[28%] md:table-cell">
                            <span className="sr-only">1-month return chart</span>
                          </th>
                          <th className="r">1 month</th>
                          <th className="r">3 months</th>
                          <th>Series</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(allSectors ? d.sectors : d.sectors.slice(0, SECTOR_PREVIEW)).map((s: any) => (
                          <tr key={s.sector}>
                            <td className="font-medium">{s.sector}</td>
                            <td>{s.quadrant ? <QuadrantChip quadrant={s.quadrant} /> : <Missing status={s.status} />}</td>
                            <td className="hidden md:table-cell">
                              <DivergingBar value={s.return_1m} max={sectorMax} />
                            </td>
                            <td className={`r num font-semibold ${signClass(s.return_1m)}`}>{fmtSigned(s.return_1m)}</td>
                            <td className={`r num ${signClass(s.return_3m)}`}>{fmtSigned(s.return_3m)}</td>
                            <td className="text-[13px] text-ink-3">{s.series_kind === 'composite' ? 'Composite of constituents' : s.series_kind === 'index' ? 'Sector index' : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {d.sectors.length > SECTOR_PREVIEW && (
                      <div className="flex justify-center border-t border-rule px-4 py-2">
                        <button type="button" onClick={() => setAllSectors((v) => !v)} aria-expanded={allSectors} className="rounded-md px-3 py-1 text-[13px] font-medium text-link hover:bg-surface-2">
                          {allSectors ? `Show the first ${SECTOR_PREVIEW} sectors` : `Show all ${d.sectors.length} sectors`}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </Panel>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <Movers title="Highest research scores" rows={d.top_scores} unit="score" aside="Score 0–100" />
                <Movers title="Strongest 3-month return" rows={d.top_momentum} unit="pct" />
                <Movers title="Weakest 3-month return" rows={d.weakest_momentum} unit="pct" />
              </div>

              <Panel title="Watchlist highlights" aside={<Link to="/watchlists">Open watchlists</Link>}>
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
          )
        }}
      </Async>
    </>
  )
}

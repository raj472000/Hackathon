import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, post, useApi, waitForJob } from '../api/client'
import { SourcesSchema } from '../api/schemas'
import { Async, Badge, Button, Kv, Missing, Notice, PageHeader, Panel, RefreshButton, StateBadge } from '../components/ui'
import { PROVIDER_LABEL, ago, fmtDate, fmtDateTime, fmtNum } from '../lib/format'
import { KiteSessionPanel } from '../components/BrokerLogin'

const CAP_LABEL: Record<string, string> = {
  history: 'Daily price history',
  index_history: 'Index history',
  intraday_history: 'Intraday candles',
  quote: 'Quotes',
  corporate_actions: 'Corporate actions',
  search: 'Instrument search',
  bulk_quote: 'Bulk end-of-day quotes',
  market_breadth: 'Market breadth',
  top_movers: 'Top movers',
  live_movers: 'Live movers',
  data_status: 'Feed status',
  fundamentals: 'Financial statements',
  instrument_master: 'Instrument master',
  order_placement: 'Order placement',
}

function ApiToken() {
  const [v, setV] = useState('')
  const has = Boolean(sessionStorage.getItem('api_token'))
  return (
    <div className="flex flex-wrap items-end gap-2 px-4 py-3">
      <label>
        <span className="mb-1 block font-medium">API token</span>
        <input type="password" autoComplete="off" className="field w-64" value={v} onChange={(e) => setV(e.target.value)} placeholder={has ? 'A token is set for this tab' : 'Only needed if the server requires one'} />
      </label>
      <Button
        onClick={() => {
          if (v) sessionStorage.setItem('api_token', v)
          else sessionStorage.removeItem('api_token')
          location.reload()
        }}
      >
        {v ? 'Use token' : 'Clear token'}
      </Button>
      <span className="text-xs text-ink-2">Kept for this browser tab only.</span>
    </div>
  )
}

export function Status() {
  const qc = useQueryClient()
  const q = useApi<any>(['sources'], '/sources/status', undefined, { schema: SourcesSchema as any, refetchInterval: 30_000 })
  const jobs = useApi<any>(['jobs'], '/jobs', { limit: 25 }, { refetchInterval: 5_000 })
  const audit = useApi<any>(['audit'], '/audit/events', { limit: 15 })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const refreshAll = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const { job_id } = await post('/jobs/refresh-all')
      qc.invalidateQueries({ queryKey: ['jobs'] })
      const j = await waitForJob(job_id)
      const p = j.detail?.prices
      setMsg(
        j.status === 'succeeded'
          ? { tone: p?.failed ? 'bad' : 'good', text: `Refresh finished: ${p?.ok ?? 0} instruments updated, ${p?.failed ?? 0} failed, ${p?.conflicts ?? 0} source disagreements, ${p?.broker_filled_bars ?? 0} bars filled by the broker.` }
          : { tone: 'bad', text: `Refresh ${j.status}: ${j.error ?? ''}. Previously stored data is unchanged.` },
      )
      qc.invalidateQueries()
    } catch (e) {
      setMsg({ tone: 'bad', text: e instanceof ApiError ? e.message : 'Refresh failed.' })
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageHeader
        title="Data status"
        lede="Which sources are connected, what each one can supply, how fresh the stored data is, and what the scheduled jobs did."
        actions={
          <Button variant="primary" onClick={refreshAll} busy={busy}>
            Refresh all data
          </Button>
        }
        refresh={<RefreshButton keys={[['sources'], ['jobs'], ['audit']]} action={() => post('/sources/rediscover')} label="Re-check sources" hint="Reconnect to every source, re-read its advertised tools and reload this page" />}
      />
      {msg && (
        <div className="mb-3">
          <Notice tone={msg.tone}>{msg.text}</Notice>
        </div>
      )}
      <Panel title="Zerodha Kite session" className="mb-4">
        <KiteSessionPanel />
      </Panel>
      <Async q={q} rows={12}>
        {(d) => {
          const caps = d.capabilities
          const capKeys = Array.from(new Set([...Object.keys(caps.nse_mcp ?? {}), ...Object.keys(caps.kite_mcp ?? {})]))
          const llm = d.connectors.find((c: any) => c.key === 'llm')
          return (
            <div className="space-y-4">
              <Panel title="Sources" aside={<span>Checked {ago(d.retrieved_at)} · mode: {d.data_mode}</span>}>
                <div className="scroll-x">
                  <table className="text-[13px]">
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>State</th>
                        <th>Used for</th>
                        <th className="r">Tools</th>
                        <th className="r">Latency</th>
                        <th className="r">Error rate</th>
                        <th>Last success</th>
                        <th>Last error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.cache?.note && (
                        <tr>
                          <td colSpan={8}>
                            <Notice tone="warn" title="Cache.">{d.cache.note}</Notice>
                          </td>
                        </tr>
                      )}
                      {d.connectors.map((c: any) => (
                        <tr key={c.key}>
                          <td>
                            <div className="font-medium">{c.label}</div>
                            <div className="max-w-[260px] truncate text-xs text-ink-3" title={c.url ?? c.base_url ?? ''}>
                              {c.url ?? c.base_url ?? c.directory ?? ''}
                            </div>
                          </td>
                          <td>
                            <StateBadge state={c.state} />
                            {c.auth?.state && c.auth.state !== 'unknown' && (
                              <div className="mt-1">
                                <StateBadge state={c.auth.state} label={`Session: ${c.auth.state.replace(/_/g, ' ')}`} />
                                {c.auth.checked_at && <div className="mt-0.5 text-xs text-ink-3">checked {ago(c.auth.checked_at)}</div>}
                                {c.auth.reason && <div className="mt-0.5 max-w-[220px] text-xs text-ink-2">{c.auth.reason}</div>}
                              </div>
                            )}
                          </td>
                          <td className="text-ink-2">{c.role}</td>
                          <td className="r num" title={(c.tools ?? []).join(', ')}>
                            {c.tool_count ?? <Missing />}
                            {c.blocked_tools?.length > 0 && <div className="text-xs text-ink-2">{c.blocked_tools.length} blocked</div>}
                          </td>
                          <td className="r num">{c.latency_ms == null ? <Missing reason="No call made yet" /> : `${fmtNum(c.latency_ms, 0)} ms`}</td>
                          <td className="r num">{c.error_rate == null ? <Missing reason="No call made yet" /> : `${fmtNum(c.error_rate * 100, 1)}%`}</td>
                          <td title={fmtDateTime(c.last_success_at)}>{c.last_success_at ? ago(c.last_success_at) : <Missing reason="Never succeeded in this session" />}</td>
                          <td className="max-w-[280px] text-ink-2">{c.last_error ?? c.reason ?? (c.note && c.state !== 'ok' ? c.note : '') ?? ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>

              <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Stored data">
                  <dl className="m-0 px-4 py-2">
                    <Kv k="Instruments tracked">{d.coverage.instruments}</Kv>
                    <Kv k="Instruments with metrics">{d.coverage.instruments_with_metrics}</Kv>
                    <Kv k="Instruments with financial statements">{d.coverage.instruments_with_statements}</Kv>
                    <Kv k="Latest price bar">{fmtDate(d.coverage.last_bar)}</Kv>
                    <Kv k="Instruments with stale prices">{d.coverage.stale_instruments}</Kv>
                    {Object.entries(d.coverage.bars_by_provider).map(([p, n]) => (
                      <Kv key={p} k={`Price bars from ${PROVIDER_LABEL[p] ?? p}`}>
                        {fmtNum(n as number, 0)}
                      </Kv>
                    ))}
                    <Kv k="Recorded source disagreements">{d.coverage.source_conflicts}</Kv>
                  </dl>
                </Panel>
              </div>

              <Panel title="What each market-data source supplies" aside={<span>Read from the tools each server advertises</span>}>
                <div className="scroll-x">
                  <table className="text-[13px]">
                    <thead>
                      <tr>
                        <th>Data</th>
                        <th>NSE MCP (used first)</th>
                        <th>Kite MCP (fills gaps)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {capKeys.map((k) => (
                        <tr key={k}>
                          <td className="font-medium">{CAP_LABEL[k] ?? k}</td>
                          {[caps.nse_mcp?.[k], caps.kite_mcp?.[k]].map((c: any, i) => (
                            <td key={i}>
                              {c == null ? (
                                <span className="text-ink-3">—</span>
                              ) : c.supported ? (
                                <span>
                                  <span className="text-gain" aria-hidden>
                                    ✓
                                  </span>{' '}
                                  Yes <span className="text-ink-2">via {c.tool}</span>
                                </span>
                              ) : (
                                <span>
                                  <span className="text-ink-3" aria-hidden>
                                    ✕
                                  </span>{' '}
                                  No <span className="text-ink-2">({c.reason})</span>
                                </span>
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="space-y-2 px-4 py-3 text-[13px] text-ink-2">
                  <p className="m-0">
                    <span className="font-semibold text-ink">How sources are combined. </span>
                    Order of use: {d.precedence.default.map((p: string) => PROVIDER_LABEL[p] ?? p).join(', then ')}. The broker fills a value only when NSE has none. When both report a value and differ by more than the tolerance (close {d.precedence.tolerances_pct.close}%, live price{' '}
                    {d.precedence.tolerances_pct.last_price}%, volume {d.precedence.tolerances_pct.volume}%), the NSE value is kept and the disagreement is recorded and shown on the instrument page.
                  </p>
                  {d.terms.map((t: string) => (
                    <p key={t} className="m-0">
                      {t}
                    </p>
                  ))}
                </div>
              </Panel>

              <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Language model">
                  <dl className="m-0 px-4 py-2">
                    <Kv k="State">
                      <StateBadge state={llm?.state} />
                    </Kv>
                    <Kv k="Provider">{llm?.provider ?? <Missing />}</Kv>
                    <Kv k="Model id">{llm?.model_id ?? <Missing reason={llm?.reason ?? 'The model id is not set'} />}</Kv>
                    <Kv k="Route">{llm?.base_url}</Kv>
                    <Kv k="Verified against the account">{llm?.verified ? (llm.verified.ok ? `Yes, ${ago(llm.verified.checked_at)}` : `No: ${llm.verified.reason}${llm.verified.suggestions?.length ? ` Similar ids on this route: ${llm.verified.suggestions.join(', ')}.` : ''}`) : 'Not checked'}</Kv>
                    <Kv k="Calls today (UTC)">
                      {llm?.usage.calls} of {llm?.usage.daily_budget}
                    </Kv>
                    <Kv k="Tokens today">
                      {fmtNum(llm?.usage.total_tokens, 0)} of {fmtNum(llm?.usage.daily_token_budget, 0)}
                    </Kv>
                    <Kv k="Errors today">{llm?.usage.errors}</Kv>
                    <Kv k="Cost today">{llm?.usage.cost_usd == null ? <Missing reason="Not reported by the provider yet" /> : `US$${fmtNum(llm.usage.cost_usd, 4)}`}</Kv>
                    <Kv k="Average latency">{llm?.usage.latency_ms_avg == null ? <Missing reason="No call made yet" /> : `${fmtNum(llm.usage.latency_ms_avg, 0)} ms`}</Kv>
                  </dl>
                  {llm?.reason && <p className="m-0 px-4 pb-3 text-[13px] text-ink-2">{llm.reason}. Research and concall analysis use rule-based fallbacks until it is configured.</p>}
                  <p className="m-0 px-4 pb-3 text-xs text-ink-2">{llm?.usage.note}</p>
                </Panel>
                <Panel title="Schedule" aside={<span>{d.scheduler.enabled ? 'Scheduler running' : 'Scheduler disabled in this process'}</span>}>
                  <table className="text-[13px]">
                    <thead>
                      <tr>
                        <th>Job</th>
                        <th>Cron</th>
                        <th>Next run</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.scheduler.jobs.map((j: any) => (
                        <tr key={j.id}>
                          <td>{j.description}</td>
                          <td className="num text-ink-2">{j.cron}</td>
                          <td>{fmtDateTime(j.next_run)}</td>
                        </tr>
                      ))}
                      {d.scheduler.jobs.length === 0 && (
                        <tr>
                          <td colSpan={3} className="text-ink-2">
                            No scheduler in this process. In deployment the worker service runs the schedule.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </Panel>
              </div>
            </div>
          )
        }}
      </Async>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Job history">
          <Async q={jobs} rows={5}>
            {(d) => (
              <div className="scroll-x">
                <table className="text-[13px]">
                  <thead>
                    <tr>
                      <th>Job</th>
                      <th>State</th>
                      <th>Trigger</th>
                      <th>Started</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((j: any) => (
                      <tr key={j.job_id}>
                        <td className="font-medium">{j.kind.replace(/_/g, ' ')}</td>
                        <td>
                          <StateBadge state={j.status} />
                        </td>
                        <td className="text-ink-2">{j.trigger}</td>
                        <td title={fmtDateTime(j.started_at)}>{ago(j.started_at)}</td>
                        <td className="max-w-[260px] text-ink-2">{j.error ?? (j.finished_at ? `Finished ${fmtDateTime(j.finished_at)}` : 'Running')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Async>
        </Panel>
        <div className="space-y-4">
          <Panel title="Recent activity">
            <Async q={audit} rows={4}>
              {(d) =>
                d.items.length === 0 ? (
                  <p className="m-0 px-4 py-3 text-ink-2">No user actions recorded yet.</p>
                ) : (
                  <ul className="m-0 list-none p-0 text-[13px]">
                    {d.items.map((a: any) => (
                      <li key={a.id} className="flex justify-between gap-3 border-b border-rule px-4 py-1.5 last:border-0">
                        <span>
                          <Badge>{a.action}</Badge> {a.entity && <span className="text-ink-2">{a.entity} {a.entity_id}</span>}
                        </span>
                        <span className="whitespace-nowrap text-ink-2">{fmtDateTime(a.at)}</span>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Async>
          </Panel>
          <Panel title="Access">
            <ApiToken />
          </Panel>
        </div>
      </div>
    </>
  )
}

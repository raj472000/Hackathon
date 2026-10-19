import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, post, useAction, useApi } from '../api/client'
import { InstrumentSearch, type InstrumentLite } from '../components/InstrumentSearch'
import { Async, Badge, Button, Confirm, EmptyState, ErrorState, Missing, PageHeader, Panel, RefreshButton, RiskBadge, StateBadge } from '../components/ui'
import { fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'
import { instrumentPath } from './Overview'

const KIND_LABEL: Record<string, string> = {
  price_move: 'Price move',
  indicator_cross: 'Metric crosses a level',
  theme_membership: 'Theme membership changes',
  sector_quadrant: 'Sector changes rotation quadrant',
  data_quality: 'Price data goes stale or missing',
  filing: 'New financial statements ingested',
}

function describe(a: any, metrics: any[]): string {
  const p = a.params
  switch (a.kind) {
    case 'price_move':
      return `${a.symbol} moves ${p.pct}% or more over ${p.window}`
    case 'indicator_cross':
      return `${a.symbol}: ${metrics.find((m) => m.key === p.metric)?.label ?? p.metric} crosses ${p.op === 'gt' ? 'above' : 'below'} ${p.value}`
    case 'theme_membership':
      return `Theme ${p.theme_id}${a.symbol ? ` gains or loses ${a.symbol}` : ' membership changes'}`
    case 'sector_quadrant':
      return `${p.sector} changes quadrant`
    case 'data_quality':
      return `${a.symbol}: price data stale or missing`
    case 'filing':
      return `${a.symbol}: new statements`
    default:
      return a.kind
  }
}

function NewAlert({ metrics, sectors, themes }: { metrics: any[]; sectors: string[]; themes: any[] }) {
  const [kind, setKind] = useState('price_move')
  const [inst, setInst] = useState<InstrumentLite | null>(null)
  const [p, setP] = useState<Record<string, string>>({ window: '1W', pct: '5', metric: 'rsi_14', op: 'gt', value: '70', sector: '', theme_id: '' })
  const needsInst = ['price_move', 'indicator_cross', 'data_quality', 'filing'].includes(kind)
  const create = useAction<void, any>(() => {
    const params: Record<string, unknown> = kind === 'price_move' ? { window: p.window, pct: Number(p.pct) } : kind === 'indicator_cross' ? { metric: p.metric, op: p.op, value: Number(p.value) } : kind === 'sector_quadrant' ? { sector: p.sector } : kind === 'theme_membership' ? { theme_id: Number(p.theme_id) } : {}
    // The key makes a repeated submit of the same alert a no-op on the server.
    const key = `${kind}:${inst?.id ?? 'all'}:${JSON.stringify(params)}`.slice(0, 80).padEnd(8, '_')
    return post('/alerts', { kind, instrument_id: needsInst || kind === 'theme_membership' ? (inst?.id ?? null) : null, params, idempotency_key: key })
  }, [['alerts']])
  const set = (k: string, v: string) => setP({ ...p, [k]: v })
  return (
    <div className="space-y-3 px-4 py-4">
      <label className="block">
        <span className="mb-1 block font-medium">Tell me when</span>
        <select className="field w-full" value={kind} onChange={(e) => setKind(e.target.value)}>
          {Object.entries(KIND_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      {(needsInst || kind === 'theme_membership') && (
        <div>
          <span className="mb-1 block font-medium">Instrument{kind === 'theme_membership' ? ' (optional)' : ''}</span>
          {inst ? (
            <div className="flex items-center gap-2">
              <Badge tone="accent">
                {inst.exchange}:{inst.symbol}
              </Badge>
              <Button variant="quiet" onClick={() => setInst(null)}>
                Change
              </Button>
            </div>
          ) : (
            <InstrumentSearch equitiesOnly onPick={setInst} />
          )}
        </div>
      )}
      {kind === 'price_move' && (
        <div className="flex flex-wrap items-end gap-2">
          <label>
            <span className="mb-1 block font-medium">Move of at least (%)</span>
            <input className="field num w-28" inputMode="decimal" value={p.pct} onChange={(e) => set('pct', e.target.value)} />
          </label>
          <label>
            <span className="mb-1 block font-medium">Over</span>
            <select className="field" value={p.window} onChange={(e) => set('window', e.target.value)}>
              <option value="1W">1 week</option>
              <option value="1M">1 month</option>
              <option value="3M">3 months</option>
            </select>
          </label>
        </div>
      )}
      {kind === 'indicator_cross' && (
        <div className="flex flex-wrap items-end gap-2">
          <label>
            <span className="mb-1 block font-medium">Metric</span>
            <select className="field max-w-[240px]" value={p.metric} onChange={(e) => set('metric', e.target.value)}>
              {metrics.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block font-medium">Crosses</span>
            <select className="field" value={p.op} onChange={(e) => set('op', e.target.value)}>
              <option value="gt">above</option>
              <option value="lt">below</option>
            </select>
          </label>
          <label>
            <span className="mb-1 block font-medium">Level</span>
            <input className="field num w-28" inputMode="decimal" value={p.value} onChange={(e) => set('value', e.target.value)} />
          </label>
        </div>
      )}
      {kind === 'sector_quadrant' && (
        <label className="block">
          <span className="mb-1 block font-medium">Sector</span>
          <select className="field w-full" value={p.sector} onChange={(e) => set('sector', e.target.value)}>
            <option value="">Choose a sector</option>
            {sectors.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      )}
      {kind === 'theme_membership' && (
        <label className="block">
          <span className="mb-1 block font-medium">Theme</span>
          <select className="field w-full" value={p.theme_id} onChange={(e) => set('theme_id', e.target.value)}>
            <option value="">Choose a theme</option>
            {themes.filter((t) => t.available).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <Button variant="primary" busy={create.isPending} disabled={(needsInst && !inst) || (kind === 'sector_quadrant' && !p.sector) || (kind === 'theme_membership' && !p.theme_id)} onClick={() => create.mutate()}>
        Create alert
      </Button>
      {create.data && <span className="ml-3 text-[13px] text-ink-2">{create.data.created ? 'Alert created.' : 'This alert already exists.'}</span>}
      {create.error && <ErrorState error={create.error} />}
    </div>
  )
}

export function Watchlists() {
  const wl = useApi<any>(['watchlists'], '/watchlists')
  const al = useApi<any>(['alerts'], '/alerts')
  const cat = useApi<any>(['metric-catalog'], '/screener/metrics', undefined, { staleTime: 3_600_000 })
  const sectors = useApi<any>(['sectors'], '/sectors')
  const themes = useApi<any>(['themes'], '/themes')
  const [name, setName] = useState('')
  const [del, setDel] = useState<{ type: 'watchlist' | 'alert'; id: number; label: string } | null>(null)
  const createList = useAction<void, any>(() => post('/watchlists', { name }).then((r) => (setName(''), r)), [['watchlists']])
  const addItem = useAction<{ wid: number; inst: InstrumentLite }, any>(({ wid, inst }) => post(`/watchlists/${wid}/items`, { instrument: String(inst.id) }), [['watchlists']])
  const removeItem = useAction<{ wid: number; item: number }, any>(({ wid, item }) => api(`/watchlists/${wid}/items/${item}`, { method: 'DELETE' }), [['watchlists']])
  const toggleAlert = useAction<{ id: number; status: string }, any>(({ id, status }) => api(`/alerts/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }), [['alerts']])
  const doDelete = useAction<void, any>(() => api(del!.type === 'watchlist' ? `/watchlists/${del!.id}` : `/alerts/${del!.id}`, { method: 'DELETE', params: { confirm: true } }).then((r) => (setDel(null), r)), [['watchlists'], ['alerts']])
  return (
    <>
      <PageHeader
        title="Watchlists and alerts"
        lede="Track instruments and get notified in this app when a condition is met. Nothing here can place an order."
        refresh={<RefreshButton keys={[['watchlists'], ['alerts']]} hint="Reload watchlist prices and alert states" />}
      />
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <label>
          <span className="mb-1 block font-medium">New watchlist</span>
          <input className="field w-64" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Name" />
        </label>
        <Button variant="primary" disabled={!name.trim()} busy={createList.isPending} onClick={() => createList.mutate()}>
          Create watchlist
        </Button>
        {createList.error && <span className="text-loss">{createList.error.message}</span>}
      </div>
      {addItem.error && <ErrorState error={addItem.error} />}
      <Async q={wl} rows={6}>
        {(d) =>
          d.items.length === 0 ? (
            <Panel className="mb-4">
              <EmptyState title="No watchlists yet">Create one above, then add instruments to it.</EmptyState>
            </Panel>
          ) : (
            d.items.map((w: any) => (
              <Panel
                key={w.id}
                title={w.name}
                className="mb-4"
                aside={
                  <>
                    <span>{w.items.length} instruments · INR</span>
                    <Button variant="danger" onClick={() => setDel({ type: 'watchlist', id: w.id, label: w.name })}>
                      Delete list
                    </Button>
                  </>
                }
              >
                <div className="max-w-sm px-4 pt-3">
                  <InstrumentSearch equitiesOnly placeholder={`Add an instrument to ${w.name}`} onPick={(inst) => addItem.mutate({ wid: w.id, inst })} />
                </div>
                {w.items.length === 0 ? (
                  <EmptyState title="This list is empty">Search above to add the first instrument.</EmptyState>
                ) : (
                  <div className="scroll-x mt-2">
                    <table className="text-[13px]">
                      <thead>
                        <tr>
                          <th>Instrument</th>
                          <th className="r">Close (₹)</th>
                          <th className="r">1 week</th>
                          <th className="r">1 month</th>
                          <th className="r">Score</th>
                          <th>Labels</th>
                          <th>Data</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {w.items.map((it: any) => (
                          <tr key={it.item_id}>
                            <td>
                              <Link to={instrumentPath(it.symbol)} className="font-semibold">
                                {it.symbol}
                              </Link>
                              <div className="text-xs text-ink-2">{it.sector}</div>
                            </td>
                            <td className="r num">{it.close == null ? <Missing reason="No price history ingested" /> : fmtNum(it.close)}</td>
                            <td className={`r num ${signClass(it.return_1w)}`}>{fmtSigned(it.return_1w)}</td>
                            <td className={`r num ${signClass(it.return_1m)}`}>{fmtSigned(it.return_1m)}</td>
                            <td className="r num">{it.overall_score != null ? Math.round(it.overall_score) : it.partial_score != null ? `${Math.round(it.partial_score)}*` : <Missing reason="Insufficient data" />}</td>
                            <td>
                              <div className="flex flex-wrap gap-1">
                                {it.stance && <StateBadge state={it.stance} label={it.stance} />}
                                {it.risk_label && <RiskBadge label={it.risk_label} />}
                                {it.entry_state && <StateBadge state={it.entry_state} label={`Entry: ${it.entry_state}`} />}
                              </div>
                            </td>
                            <td>{it.freshness ? <StateBadge state={it.freshness} label={`${it.freshness === 'stale' ? 'Stale' : 'To'} ${fmtDate(it.last_bar)}`} /> : <Missing />}</td>
                            <td className="r">
                              <Button variant="quiet" onClick={() => removeItem.mutate({ wid: w.id, item: it.item_id })} aria-label={`Remove ${it.symbol} from ${w.name}`}>
                                Remove
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
            ))
          )
        }
      </Async>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Panel title="New alert">
          <NewAlert metrics={cat.data?.metrics ?? []} sectors={(sectors.data?.items ?? []).map((s: any) => s.sector)} themes={themes.data?.items ?? []} />
        </Panel>
        <div className="space-y-4">
          <Panel title="Alerts">
            <Async q={al} rows={4}>
              {(d) =>
                d.items.length === 0 ? (
                  <EmptyState title="No alerts set" />
                ) : (
                  <ul className="m-0 list-none p-0">
                    {d.items.map((a: any) => (
                      <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-4 py-2.5 last:border-0">
                        <div>
                          <div className="font-medium">{describe(a, cat.data?.metrics ?? [])}</div>
                          <div className="text-xs text-ink-2">
                            {KIND_LABEL[a.kind]} · {a.last_triggered_at ? `last triggered ${fmtDateTime(a.last_triggered_at)}` : 'not triggered yet'}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge tone={a.status === 'active' ? 'good' : 'neutral'}>{a.status === 'active' ? 'Active' : 'Paused'}</Badge>
                          <Button onClick={() => toggleAlert.mutate({ id: a.id, status: a.status === 'active' ? 'paused' : 'active' })}>{a.status === 'active' ? 'Pause' : 'Resume'}</Button>
                          <Button variant="danger" onClick={() => setDel({ type: 'alert', id: a.id, label: describe(a, cat.data?.metrics ?? []) })}>
                            Delete
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Async>
          </Panel>
          <Panel title="Notification history">
            <Async q={al} rows={3}>
              {(d) => (
                <>
                  {d.history.length === 0 ? (
                    <EmptyState title="Nothing has triggered yet">Alerts are checked every 15 minutes and after each data refresh.</EmptyState>
                  ) : (
                    <ul className="m-0 list-none p-0 text-[13px]">
                      {d.history.map((h: any) => (
                        <li key={h.id} className="border-b border-rule px-4 py-2 last:border-0">
                          <div>{h.message}</div>
                          <div className="text-xs text-ink-2">
                            {fmtDateTime(h.triggered_at)} · recorded in app
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="m-0 border-t border-rule px-4 py-2 text-xs text-ink-2">{d.delivery_note}</p>
                </>
              )}
            </Async>
          </Panel>
        </div>
      </div>
      <Confirm
        open={del !== null}
        title={del?.type === 'watchlist' ? 'Delete this watchlist?' : 'Delete this alert?'}
        body={<>“{del?.label}” will be removed{del?.type === 'alert' ? ' along with its notification history' : ''}. This cannot be undone.</>}
        confirmLabel={del?.type === 'watchlist' ? 'Delete watchlist' : 'Delete alert'}
        danger
        busy={doDelete.isPending}
        onConfirm={() => doDelete.mutate()}
        onClose={() => setDel(null)}
      />
    </>
  )
}

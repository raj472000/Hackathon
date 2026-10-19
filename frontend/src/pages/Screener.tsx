import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, useApi } from '../api/client'
import { ScreenerSchema, type Screener as ScreenerData } from '../api/schemas'
import { PillarGlyph, pillarScores } from '../components/Score'
import { Async, Badge, Button, Dialog, EmptyState, Missing, Notice, PageHeader, Panel, RefreshButton, RiskBadge, StateBadge, cx } from '../components/ui'
import { fmtDate, fmtDateTime, fmtMetric } from '../lib/format'
import { instrumentPath } from './Overview'

export type FilterRow = { metric: string; op: string; value: string; value2?: string }
type Saved = { name: string; filters: FilterRow[]; sectors: string[]; columns: string[]; sort: string; order: string }
const OPS: Record<string, string> = { gt: 'greater than', gte: 'at least', lt: 'less than', lte: 'at most', eq: 'equals', between: 'between' }
const KEY = 'saved-screens-v1'

function loadSaved(): Saved[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]')
  } catch {
    return []
  }
}

export function toApiFilters(rows: FilterRow[]) {
  return rows
    .filter((r) => r.metric && r.value !== '' && !Number.isNaN(Number(r.value)) && (r.op !== 'between' || (r.value2 !== undefined && r.value2 !== '' && !Number.isNaN(Number(r.value2)))))
    .map((r) => ({ metric: r.metric, op: r.op, value: Number(r.value), ...(r.op === 'between' ? { value2: Number(r.value2) } : {}) }))
}

/** Metric chooser with a search box: the catalog has about 100 metrics, too many to scroll through. */
function MetricPicker({ value, metrics, onChange }: { value: string; metrics: any[]; onChange: (k: string) => void }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  const ql = text.trim().toLowerCase()
  const hits = metrics.filter((m) => !ql || m.label.toLowerCase().includes(ql) || m.key.includes(ql) || m.group.includes(ql))
  const pick = (k: string) => {
    onChange(k)
    setOpen(false)
    setText('')
  }
  const current = metrics.find((m) => m.key === value)
  return (
    <div ref={box} className="relative">
      <button type="button" className="field min-w-[220px] text-left" aria-haspopup="listbox" aria-expanded={open} aria-label="Metric" onClick={() => setOpen(!open)}>
        {current?.label ?? 'Choose a metric'} <span aria-hidden className="float-right text-ink-3">▾</span>
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-[300px] rounded-md border border-rule-strong bg-surface shadow-lg">
          <input
            autoFocus
            className="field m-2 w-[calc(100%-1rem)]"
            placeholder="Search metrics…"
            aria-label="Search metrics"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false)
              if (e.key === 'Enter' && hits[0]) {
                e.preventDefault()
                pick(hits[0].key)
              }
            }}
          />
          <ul role="listbox" className="m-0 max-h-72 list-none overflow-y-auto p-0 pb-1">
            {hits.length === 0 && <li className="px-3 py-2 text-xs text-ink-2">No metric matches “{text}”.</li>}
            {hits.map((m) => (
              <li key={m.key} role="option" aria-selected={m.key === value}>
                <button type="button" className={cx('flex w-full justify-between gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-accent-soft', m.key === value && 'font-semibold')} onClick={() => pick(m.key)}>
                  <span>{m.label}</span>
                  <span className="text-xs text-ink-3">{m.group}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function FilterBuilder({ rows, onChange, metrics }: { rows: FilterRow[]; onChange: (r: FilterRow[]) => void; metrics: any[] }) {
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {}
    for (const m of metrics) (g[m.group] ??= []).push(m)
    return g
  }, [metrics])
  const set = (i: number, patch: Partial<FilterRow>) => onChange(rows.map((r, n) => (n === i ? { ...r, ...patch } : r)))
  return (
    <div className="space-y-2">
      {rows.length === 0 && <p className="m-0 text-ink-2">No filters. Every instrument with metrics is listed.</p>}
      {rows.map((r, i) => {
        const unit = metrics.find((m) => m.key === r.metric)?.unit
        return (
          <div key={i} className="flex flex-wrap items-center gap-2" role="group" aria-label={`Filter ${i + 1}`}>
            <MetricPicker value={r.metric} metrics={Object.values(groups).flat()} onChange={(k) => set(i, { metric: k })} />
            <select className="field" aria-label="Condition" value={r.op} onChange={(e) => set(i, { op: e.target.value })}>
              {Object.entries(OPS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <input className="field num w-28" aria-label="Value" inputMode="decimal" value={r.value} onChange={(e) => set(i, { value: e.target.value })} />
            {r.op === 'between' && (
              <>
                <span className="text-ink-2">and</span>
                <input className="field num w-28" aria-label="Upper value" inputMode="decimal" value={r.value2 ?? ''} onChange={(e) => set(i, { value2: e.target.value })} />
              </>
            )}
            <span className="text-xs text-ink-2">{unit === 'pct' ? 'percent' : unit === 'x' ? 'times' : unit === 'currency' ? 'INR crore' : unit === 'days' ? 'days' : unit === 'score' ? '0–100' : ''}</span>
            <Button variant="quiet" onClick={() => onChange(rows.filter((_, n) => n !== i))} aria-label={`Remove filter ${i + 1}`}>
              Remove
            </Button>
          </div>
        )
      })}
      <Button onClick={() => onChange([...rows, { metric: metrics[0]?.key ?? 'roe', op: 'gte', value: '' }])}>Add filter</Button>
    </div>
  )
}

export function Screener() {
  const cat = useApi<any>(['metric-catalog'], '/screener/metrics', undefined, { staleTime: 3_600_000 })
  const sectorsQ = useApi<any>(['sectors'], '/sectors')
  const [filters, setFilters] = useState<FilterRow[]>([])
  const [sectors, setSectors] = useState<string[]>([])
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const qc = useQueryClient()
  // Hovering a row loads the stock page's stored data in advance, so the click opens it at once.
  const prefetch = (exchange: string, symbol: string) => {
    const ref = `${exchange}:${symbol}`
    qc.prefetchQuery({ queryKey: ['instrument', ref, { quote: false }], queryFn: () => api(`/instruments/${encodeURIComponent(ref)}`, { params: { quote: false } }), staleTime: 30_000 })
  }
  const [sort, setSort] = useState('overall_score')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  // The search box filters as you type, with a short pause so each keystroke does not send a request.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchText.trim())
      setPage(1)
    }, 250)
    return () => clearTimeout(t)
  }, [searchText])
  const [columns, setColumns] = useState<string[] | null>(null)
  const [pickCols, setPickCols] = useState(false)
  const [saved, setSaved] = useState<Saved[]>(loadSaved)
  const cols = columns ?? cat.data?.default_columns ?? []
  // Pillar scores are always fetched so the glyph can be drawn even when those columns are hidden.
  const fetchCols = useMemo(() => Array.from(new Set([...cols, 'fundamentals_score', 'valuation_score', 'momentum_score', 'risk_score', 'context_score'])), [cols])
  const params = {
    filters: JSON.stringify(toApiFilters(filters)),
    sectors: sectors.join(','),
    q: search,
    sort,
    order,
    page,
    page_size: 25,
    columns: fetchCols.join(','),
  }
  const q = useApi<ScreenerData>(['screener'], cat.data ? '/screener' : null, params, { schema: ScreenerSchema as any, placeholderData: (p: any) => p })
  const exportUrl = `/api/v1/screener/export.csv?${new URLSearchParams({ filters: params.filters, sectors: params.sectors, q: search, sort, order, columns: cols.join(',') })}`
  const reset = () => setPage(1)
  const sortBy = (k: string) => {
    if (sort === k) setOrder(order === 'desc' ? 'asc' : 'desc')
    else {
      setSort(k)
      setOrder(k === 'symbol' || k === 'sector' ? 'asc' : 'desc')
    }
    reset()
  }
  const save = () => {
    const name = window.prompt('Name this screen')?.trim()
    if (!name) return
    const next = [...saved.filter((s) => s.name !== name), { name, filters, sectors, columns: cols, sort, order }]
    setSaved(next)
    try {
      localStorage.setItem(KEY, JSON.stringify(next))
    } catch {
      /* storage unavailable: the screen stays for this session only */
    }
  }
  const load = (s: Saved) => {
    setFilters(s.filters)
    setSectors(s.sectors)
    setColumns(s.columns)
    setSort(s.sort)
    setOrder(s.order as 'asc' | 'desc')
    reset()
  }
  const remove = (name: string) => {
    if (!window.confirm(`Delete the saved screen “${name}”?`)) return
    const next = saved.filter((s) => s.name !== name)
    setSaved(next)
    try {
      localStorage.setItem(KEY, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }
  const th = (k: string, label: string, right = false) => (
    <th className={cx(right && 'r')} aria-sort={sort === k ? (order === 'desc' ? 'descending' : 'ascending') : 'none'}>
      <button className="inline-flex items-center gap-1 font-medium hover:text-ink" onClick={() => sortBy(k)}>
        {label}
        <span aria-hidden className={sort === k ? 'text-accent' : 'text-ink-3'}>
          {sort === k ? (order === 'desc' ? '▼' : '▲') : '↕'}
        </span>
      </button>
    </th>
  )
  return (
    <>
      <PageHeader
        title="Screener"
        lede="Filter the tracked NSE universe on any computed metric. Missing values are shown as missing and never satisfy a filter."
        actions={
          <>
            <Button onClick={() => setPickCols(true)}>Columns</Button>
            <Button onClick={save}>Save screen</Button>
            <a href={exportUrl} className="inline-flex min-h-[34px] items-center rounded-md border border-rule-strong bg-surface px-3 text-sm font-medium text-ink no-underline hover:border-ink-3">
              Export CSV
            </a>
          </>
        }
        refresh={<RefreshButton keys={[['screener'], ['sectors']]} hint="Reload the screen with the latest computed metrics" />}
      />
      <Panel className="mb-4">
        <div className="grid gap-4 px-4 py-3 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div>
            <h2 className="mb-2 text-[15px] font-semibold">Filters</h2>
            {cat.data ? <FilterBuilder rows={filters} metrics={cat.data.metrics} onChange={(r) => (setFilters(r), reset())} /> : <div className="skeleton h-8 w-64" />}
          </div>
          <div className="space-y-3">
            <fieldset className="m-0 border-0 p-0">
              <legend className="mb-1 font-medium">Sectors</legend>
              <div className="flex flex-wrap gap-1.5">
                {(sectorsQ.data?.items ?? []).map((s: any) => {
                  const on = sectors.includes(s.sector)
                  return (
                    <button key={s.sector} aria-pressed={on} onClick={() => (setSectors(on ? sectors.filter((x) => x !== s.sector) : [...sectors, s.sector]), reset())} className={cx('rounded border px-2 py-1 text-xs', on ? 'border-accent bg-accent-soft font-semibold' : 'border-rule-strong bg-surface text-ink-2')}>
                      {s.sector}
                    </button>
                  )
                })}
              </div>
            </fieldset>
            {saved.length > 0 && (
              <div>
                <div className="mb-1 font-medium">Saved screens (this browser)</div>
                <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
                  {saved.map((s) => (
                    <li key={s.name} className="inline-flex items-center rounded border border-rule-strong">
                      <button className="px-2 py-1 text-xs" onClick={() => load(s)}>
                        {s.name}
                      </button>
                      <button className="border-l border-rule-strong px-1.5 py-1 text-xs text-ink-2" aria-label={`Delete saved screen ${s.name}`} onClick={() => remove(s.name)}>
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </Panel>

      <Panel
        title={q.data ? `${q.data.total} of ${q.data.universe} instruments` : 'Results'}
        aside={q.data && <span title={fmtDateTime(q.data.computed_at)}>Metrics as of {fmtDate(q.data.as_of)} · NSE · INR</span>}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-2.5">
          <label htmlFor="scr-q" className="sr-only">
            Search the results by symbol or name
          </label>
          <div className="relative w-full max-w-sm">
            <svg aria-hidden width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3">
              <circle cx="7" cy="7" r="4.8" />
              <path d="M10.6 10.6 14 14" />
            </svg>
            <input id="scr-q" type="search" className="field w-full pl-8" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={filters.length || sectors.length ? 'Search within the filtered results…' : 'Search by symbol or name…'} data-testid="screener-search" />
          </div>
          {search && (
            <span className="text-xs text-ink-2">
              Showing matches for “{search}”{filters.length || sectors.length ? ' within the filters' : ''}
            </span>
          )}
        </div>
        <Async q={q} rows={12}>
          {(d) => (
            <>
              {d.excluded_for_missing_filter_metric > 0 && (
                <div className="px-4 pt-3">
                  <Notice tone="warn">
                    {d.excluded_for_missing_filter_metric} instrument{d.excluded_for_missing_filter_metric === 1 ? ' is' : 's are'} left out because a filtered metric is not available for {d.excluded_for_missing_filter_metric === 1 ? 'it' : 'them'}.
                  </Notice>
                </div>
              )}
              {d.items.length === 0 ? (
                <EmptyState title={d.universe === 0 ? 'No metrics computed yet' : 'No instrument matches these filters'} action={d.universe === 0 ? <Link to="/status">Open Data status</Link> : <Button onClick={() => (setFilters([]), setSectors([]), setSearchText(''), setSearch(''))}>Clear filters</Button>}>
                  {d.universe === 0 ? 'Ingest prices first; the screener reads the latest computed metrics.' : 'Loosen a threshold or remove a filter.'}
                </EmptyState>
              ) : (
                <div className="scroll-x">
                  <table className="text-[13px]">
                    <caption className="sr-only">Screener results. Percent columns are percentages, x columns are multiples, currency is INR.</caption>
                    <thead>
                      <tr>
                        {th('symbol', 'Instrument')}
                        {th('sector', 'Sector')}
                        <th>Pillars</th>
                        <th>Stance</th>
                        {d.columns.filter((c) => cols.includes(c.key)).map((c) => (
                          <th key={c.key} className="r" aria-sort={sort === c.key ? (order === 'desc' ? 'descending' : 'ascending') : 'none'} title={c.unit === 'pct' ? 'Percent' : c.unit === 'x' ? 'Multiple' : c.unit}>
                            <button className="inline-flex items-center gap-1 font-medium hover:text-ink" onClick={() => sortBy(c.key)}>
                              {c.label}
                              <span aria-hidden className={sort === c.key ? 'text-accent' : 'text-ink-3'}>
                                {sort === c.key ? (order === 'desc' ? '▼' : '▲') : '↕'}
                              </span>
                            </button>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {d.items.map((it: any) => (
                        <tr key={it.instrument_id} onMouseEnter={() => prefetch(it.exchange, it.symbol)} onFocus={() => prefetch(it.exchange, it.symbol)}>
                          <td>
                            <Link to={instrumentPath(it.symbol, it.exchange)} className="font-semibold">
                              {it.symbol}
                            </Link>
                            <div className="max-w-[220px] truncate text-xs text-ink-2">{it.name}</div>
                          </td>
                          <td className="text-ink-2">{it.sector}</td>
                          <td>
                            <PillarGlyph scores={pillarScores(it.values)} />
                          </td>
                          <td>
                            <div className="flex flex-col items-start gap-1">
                              <StateBadge state={it.stance} label={it.stance} />
                              <RiskBadge label={it.risk_label} />
                              {it.freshness === 'stale' && <Badge tone="warn">Stale to {fmtDate(it.last_bar)}</Badge>}
                            </div>
                          </td>
                          {d.columns.filter((c) => cols.includes(c.key)).map((c) => (
                            <td key={c.key} className="r num">
                              {it.values[c.key] == null ? (
                                c.key === 'overall_score' && it.partial_score != null ? (
                                  <span title="Partial score: not all five pillars have enough data">{Math.round(it.partial_score)}*</span>
                                ) : (
                                  <Missing {...it.missing[c.key]} />
                                )
                              ) : (
                                fmtMetric(c.key, it.values[c.key], c.unit)
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-rule px-4 py-2.5 text-[13px]">
                <span className="text-ink-2">
                  Page {d.page} of {Math.max(1, Math.ceil(d.total / d.page_size))}. “—” means the value is unavailable; hover it for the reason. * marks a partial score.
                </span>
                <div className="flex gap-2">
                  <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>
                    Previous
                  </Button>
                  <Button disabled={page * d.page_size >= d.total} onClick={() => setPage(page + 1)}>
                    Next
                  </Button>
                </div>
              </div>
            </>
          )}
        </Async>
      </Panel>

      <Dialog open={pickCols} title="Choose columns" onClose={() => setPickCols(false)} footer={<Button variant="primary" onClick={() => setPickCols(false)}>Done</Button>}>
        <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          {(cat.data?.metrics ?? []).map((m: any) => (
            <label key={m.key} className="flex items-center gap-2 py-0.5">
              <input type="checkbox" checked={cols.includes(m.key)} onChange={(e) => setColumns(e.target.checked ? [...cols, m.key] : cols.filter((c: string) => c !== m.key))} />
              <span>{m.label}</span>
              <span className="text-xs text-ink-3">{m.group}</span>
            </label>
          ))}
        </div>
      </Dialog>
    </>
  )
}

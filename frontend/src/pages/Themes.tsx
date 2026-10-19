import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api, post, useAction, useApi, waitForJob } from '../api/client'
import { Async, Badge, Button, Confirm, Dialog, EmptyState, ErrorState, Missing, Notice, PageHeader, Panel, RefreshButton, RiskBadge, StateBadge, Tabs, cx } from '../components/ui'
import { fmtDate, fmtDateTime, fmtMetric, fmtNum, fmtSigned, signClass } from '../lib/format'
import { instrumentPath } from './Overview'
import { FilterBuilder, toApiFilters, type FilterRow } from './Screener'

function CreateTheme({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate()
  const cat = useApi<any>(['metric-catalog'], '/screener/metrics', undefined, { staleTime: 3_600_000 })
  const [text, setText] = useState('NSE companies with improving 3-year revenue and ROCE, low debt, positive 6-month relative strength, and moderate volatility')
  const [name, setName] = useState('')
  const [draft, setDraft] = useState<any | null>(null)
  const [rows, setRows] = useState<FilterRow[]>([])
  const [ack, setAck] = useState(false)
  const interpret = useAction<void, any>(
    () => post('/themes/interpret', { text, name: name || undefined }),
  )
  const create = useAction<void, any>(() => post('/themes', { spec: { ...draft.spec, name: name || draft.spec.name, filters: toApiFilters(rows) }, description: text, confirmed: true }), [['themes']])
  const run = async () => {
    const d = await interpret.mutateAsync()
    setDraft(d)
    setRows(d.spec.filters.map((f: any) => ({ metric: f.metric, op: f.op, value: String(f.value), value2: f.value2 == null ? undefined : String(f.value2) })))
    setName(name || d.spec.name)
    setAck(false)
  }
  const saveTheme = async () => {
    const r = await create.mutateAsync()
    onClose()
    nav(`/themes/${r.theme.id}`)
  }
  return (
    <Dialog
      open={open}
      title="Create a custom theme"
      onClose={onClose}
      footer={
        draft ? (
          <>
            <Button onClick={() => setDraft(null)}>Back</Button>
            <Button variant="primary" disabled={!ack || rows.length === 0} busy={create.isPending} onClick={saveTheme}>
              Save theme
            </Button>
          </>
        ) : (
          <Button variant="primary" busy={interpret.isPending} onClick={run} disabled={text.trim().length < 3}>
            Interpret
          </Button>
        )
      }
    >
      {!draft ? (
        <div className="space-y-3">
          <p className="m-0 text-ink-2">Describe the stocks you are looking for. The description is turned into filters you can review and change before anything is saved.</p>
          <label className="block">
            <span className="mb-1 block font-medium">Description</span>
            <textarea className="field w-full" rows={4} value={text} onChange={(e) => setText(e.target.value)} maxLength={600} />
          </label>
          <label className="block">
            <span className="mb-1 block font-medium">Theme name (optional)</span>
            <input className="field w-full" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          </label>
          {interpret.error && <ErrorState error={interpret.error} />}
        </div>
      ) : (
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block font-medium">Theme name</span>
            <input className="field w-full" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          </label>
          {draft.ambiguities.length > 0 && (
            <Notice tone="warn" title="Check these assumptions.">
              <ul className="m-0 mt-1 list-disc pl-5">
                {draft.ambiguities.map((a: string) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </Notice>
          )}
          <div>
            <div className="mb-1 font-medium">Filters that will be saved</div>
            {cat.data && <FilterBuilder rows={rows} metrics={cat.data.metrics} onChange={setRows} />}
            {rows.length === 0 && <p className="mt-2 text-loss">Add at least one filter to save a theme.</p>}
          </div>
          <p className="m-0 text-[13px] text-ink-2">
            Universe: {draft.spec.universe.exchange} equities{draft.spec.universe.sectors ? `, sectors ${draft.spec.universe.sectors.join(', ')}` : ''}. Ranked by{' '}
            {draft.spec.rank_by.map((r: any) => cat.data?.metrics.find((m: any) => m.key === r.metric)?.label ?? r.metric).join(' and ')}. Top {draft.spec.limit}.
          </p>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            <span>I have reviewed the filters and thresholds above.</span>
          </label>
          {create.error && <ErrorState error={create.error} />}
        </div>
      )}
    </Dialog>
  )
}

/** Per-theme refresh: fetch the latest bars from the sources, recompute metrics, re-rank into a new snapshot revision. */
function ThemeRefresh({ themeId, compact, onDone }: { themeId: number | string; compact?: boolean; onDone?: (j: any) => void }) {
  const qc = useQueryClient()
  const [st, setSt] = useState<{ phase: 'idle' | 'busy' | 'done' | 'error'; text?: string }>({ phase: 'idle' })
  const go = async () => {
    setSt({ phase: 'busy', text: 'Fetching latest prices' })
    try {
      const { job_id } = await post(`/themes/${themeId}/refresh`)
      const j = await waitForJob(job_id)
      if (j.status !== 'succeeded') throw new ApiError(0, 'job_failed', j.error ?? `Refresh ${j.status}`)
      const p = j.detail.prices
      setSt({ phase: 'done', text: `${j.detail.constituents} members${p ? `, prices updated for ${p.ok} of ${p.instruments}` : ''}` })
      onDone?.(j)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['themes'] }),
        qc.invalidateQueries({ queryKey: ['theme', String(themeId)] }),
        qc.invalidateQueries({ queryKey: ['theme-snapshots', String(themeId)] }),
      ])
    } catch (e) {
      setSt({ phase: 'error', text: e instanceof Error ? e.message : 'Refresh failed' })
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={go} busy={st.phase === 'busy'} className={compact ? 'min-h-[28px] px-2 py-0.5 text-xs' : undefined} title="Fetch the latest prices from the sources, recompute metrics and re-rank this theme" data-testid="theme-refresh">
        {st.phase === 'busy' ? 'Refreshing' : 'Refresh'}
      </Button>
      <span role="status" aria-live="polite" className={cx('text-xs', st.phase === 'error' ? 'text-loss' : 'text-ink-2')}>
        {st.phase !== 'idle' && st.text}
      </span>
    </div>
  )
}

const AI_EXAMPLES = ['only banks', 'low debt', 'the 10 least volatile', 'cheap versus sector peers', 'positive 6-month relative strength']

/** Narrow the theme in plain language. The model proposes filters; the server applies them deterministically. */
function AiFilter({ themeId, labels }: { themeId: string; labels: Record<string, { label: string; unit: string }> }) {
  const [query, setQuery] = useState('')
  const m = useAction<string, any>((qq) => post(`/themes/${themeId}/ai-filter`, { query: qq }))
  const run = (qq?: string) => {
    const v = (qq ?? query).trim()
    if (v.length < 3 || m.isPending) return
    setQuery(v)
    m.mutate(v)
  }
  const d = m.data
  const keys: string[] = d?.members?.[0] ? Object.keys(d.members[0].values) : []
  return (
    <Panel title="Narrow this theme with AI" className="mb-4" aside={<span>Applied to the latest data. Saved snapshots are not changed.</span>}>
      <form
        className="flex flex-col gap-2 px-4 pt-3 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault()
          run()
        }}
      >
        <label htmlFor="ai-filter" className="sr-only">
          How should the theme be narrowed?
        </label>
        <input id="ai-filter" className="field w-full" placeholder="e.g. only IT companies with low debt" value={query} onChange={(e) => setQuery(e.target.value)} maxLength={500} />
        <Button type="submit" variant="primary" busy={m.isPending} data-testid="ai-filter-submit">
          Filter
        </Button>
      </form>
      <div className="flex flex-wrap gap-1.5 px-4 pb-3 pt-2">
        {AI_EXAMPLES.map((x) => (
          <button key={x} type="button" onClick={() => run(x)} className="rounded-full border border-rule px-2.5 py-0.5 text-[12.5px] text-ink-2 hover:border-ink-3 hover:text-ink">
            {x}
          </button>
        ))}
      </div>
      {m.error && (
        <div className="px-4 pb-3">
          <Notice tone="bad">{m.error.message}</Notice>
        </div>
      )}
      {d && (
        <div className="border-t border-rule px-4 py-3" data-testid="ai-filter-result">
          <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
            <Badge tone={d.source === 'llm' ? 'good' : 'neutral'}>Filters from {d.source === 'llm' ? 'the language model' : 'rules'}</Badge>
            {d.sectors.map((s: string) => (
              <Badge key={s} tone="accent">
                {s}
              </Badge>
            ))}
            {d.added_filters.map((f: string) => (
              <Badge key={f}>{f}</Badge>
            ))}
            <span className="text-ink-2">
              {d.members.length} of {d.universe_count} instruments, metrics as of {fmtDate(d.metrics_as_of)}
            </span>
          </div>
          {d.explanation && <p className="mt-2 text-[13px] text-ink-2">{d.explanation}</p>}
          {d.llm_reason_not_used && <p className="mt-1 text-[12px] text-ink-3">The language model was not used: {d.llm_reason_not_used}</p>}
          {d.notes.map((n: string) => (
            <p key={n} className="mt-1 text-[12.5px] text-warn">
              {n}
            </p>
          ))}
          {d.members.length === 0 ? (
            <p className="mt-3 text-[13px]">
              No instrument passes these conditions. Most common reasons:{' '}
              {d.top_exclusion_reasons
                .slice(0, 3)
                .map(([r, n]: [string, number]) => `${r} (${n})`)
                .join('; ') || 'none recorded'}
              .
            </p>
          ) : (
            <div className="scroll-x mt-3 rounded-[9px] border border-rule">
              <table className="text-[13px]">
                <thead>
                  <tr>
                    <th className="r">#</th>
                    <th>Instrument</th>
                    <th className="r">Score</th>
                    {keys.map((k) => (
                      <th key={k} className="r">
                        {labels[k]?.label ?? k}
                      </th>
                    ))}
                    <th>Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {d.members.map((c: any) => (
                    <tr key={c.instrument_id}>
                      <td className="r num">{c.rank}</td>
                      <td>
                        <Link to={instrumentPath(c.symbol)} className="font-semibold">
                          {c.symbol}
                        </Link>
                        <div className="text-xs text-ink-2">{c.sector}</div>
                      </td>
                      <td className="r num">{c.score == null ? <Missing /> : fmtNum(c.score, 0)}</td>
                      {keys.map((k) => (
                        <td key={k} className="r num">
                          {c.values[k] == null ? <Missing /> : fmtMetric(k, c.values[k], labels[k]?.unit)}
                        </td>
                      ))}
                      <td>{c.risk_label ? <RiskBadge label={c.risk_label} /> : <Missing />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

export function Themes() {
  const [category, setCategory] = useState('')
  const [q, setQ] = useState('')
  const [creating, setCreating] = useState(false)
  const list = useApi<any>(['themes'], '/themes')
  return (
    <>
      <PageHeader
        title="Themes"
        lede="Rule-based screens with a fixed definition. Membership and ranks are recalculated monthly and each month's result is kept unchanged."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create custom theme
          </Button>
        }
        refresh={<RefreshButton keys={[['themes']]} hint="Reload theme memberships" />}
      />
      <Async q={list} rows={10}>
        {(d) => {
          const items = d.items.filter((t: any) => (!category || t.category === category) && (!q || `${t.name} ${t.description}`.toLowerCase().includes(q.toLowerCase())))
          const cats: string[] = d.categories.filter((c: string) => d.items.some((t: any) => t.category === c))
          return (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <input className="field w-64" placeholder="Search themes" aria-label="Search themes" value={q} onChange={(e) => setQ(e.target.value)} />
                <span className="text-[13px] text-ink-2">
                  Next scheduled refresh {fmtDateTime(d.next_refresh)} ({d.timezone})
                </span>
              </div>
              <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Theme category">
                {['', ...cats].map((c) => (
                  <button key={c || 'all'} aria-pressed={category === c} onClick={() => setCategory(c)} className={cx('rounded border px-2.5 py-1 text-[13px]', category === c ? 'border-accent bg-accent-soft font-semibold' : 'border-rule-strong bg-surface text-ink-2')}>
                    {c || 'All categories'}
                  </button>
                ))}
              </div>
              {items.length === 0 ? (
                <Panel>
                  <EmptyState title="No theme matches">Try another category or clear the search.</EmptyState>
                </Panel>
              ) : (
                cats
                  .filter((c) => !category || c === category)
                  .map((c) => {
                    const group = items.filter((t: any) => t.category === c)
                    if (group.length === 0) return null
                    return (
                      <Panel key={c} title={c} className="mb-4" aside={<span>{group.length} themes</span>}>
                        <ul className="m-0 grid list-none gap-px bg-rule p-0 md:grid-cols-2 xl:grid-cols-3">
                          {group.map((t: any) => (
                            <li key={t.id} className="bg-surface p-4">
                              <div className="flex items-start justify-between gap-2">
                                <Link to={`/themes/${t.id}`} className="text-[15px] font-semibold">
                                  {t.name}
                                </Link>
                                {!t.available && <Badge tone="warn">Needs data</Badge>}
                              </div>
                              <p className="mb-2 mt-1 text-[13px] text-ink-2">{t.description}</p>
                              {t.available ? (
                                t.latest_snapshot ? (
                                  <p className="m-0 text-[13px]">
                                    <span className="text-ink-2">{t.latest_snapshot.constituents} members · leaders </span>
                                    {t.leaders.length ? t.leaders.join(', ') : 'none qualify'}
                                    <span className="block text-xs text-ink-3">Snapshot {t.latest_snapshot.period}, refreshed {fmtDate(t.last_refreshed)}</span>
                                  </p>
                                ) : (
                                  <p className="m-0 text-[13px] text-ink-2">Not refreshed yet.</p>
                                )
                              ) : (
                                <p className="m-0 text-[13px] text-ink-2">Unavailable: needs {t.requires.filter((r: string) => !['prices', 'fundamentals', 'sector_rotation', 'corporate_actions'].includes(r)).join(', ').replace(/_/g, ' ')}, which no configured source provides.</p>
                              )}
                              {t.available && (
                                <div className="mt-3">
                                  <ThemeRefresh themeId={t.id} compact />
                                </div>
                              )}
                            </li>
                          ))}
                        </ul>
                      </Panel>
                    )
                  })
              )}
            </>
          )
        }}
      </Async>
      <CreateTheme open={creating} onClose={() => setCreating(false)} />
    </>
  )
}

export function ThemeDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const [snapId, setSnapId] = useState<number | undefined>()
  const [tab, setTab] = useState('members')
  const [msg, setMsg] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const [confirmArchive, setConfirmArchive] = useState(false)
  const q = useApi<any>(['theme', id], `/themes/${id}`, { snapshot_id: snapId })
  const hist = useApi<any>(['theme-snapshots', id], `/themes/${id}/snapshots`)
  const nav = useNavigate()
  const archive = async () => {
    await api(`/themes/${id}`, { method: 'PATCH', body: JSON.stringify({ archived: true, confirmed: true }) })
    qc.invalidateQueries({ queryKey: ['themes'] })
    nav('/themes')
  }
  return (
    <Async q={q} rows={12}>
      {(d) => {
        const t = d.theme
        const s = d.snapshot
        const labels = d.metric_labels
        const valueKeys: string[] = s?.constituents[0] ? Object.keys(s.constituents[0].values) : []
        return (
          <>
            <p className="mb-2 text-[13px]">
              <Link to="/themes">Themes</Link> <span className="text-ink-3">/</span> {t.category}
            </p>
            <PageHeader
              title={t.name}
              lede={t.description}
              actions={
                <>
                  {t.available && (
                    <ThemeRefresh
                      themeId={id as string}
                      onDone={(j) => {
                        setSnapId(undefined)
                        setMsg({ tone: 'good', text: `Fetched the latest data and re-ranked: snapshot ${j.detail.period} revision ${j.detail.revision} with ${j.detail.constituents} members. Earlier snapshots are unchanged.` })
                      }}
                    />
                  )}
                  {!t.is_builtin && (
                    <Button variant="danger" onClick={() => setConfirmArchive(true)}>
                      Archive
                    </Button>
                  )}
                </>
              }
            />
            {msg && (
              <div className="mb-3">
                <Notice tone={msg.tone}>{msg.text}</Notice>
              </div>
            )}
            {t.available && <AiFilter themeId={id as string} labels={labels} />}
            {!s ? (
              <Panel>
                <EmptyState title={t.available ? 'No snapshot yet' : 'This theme cannot be calculated yet'}>{d.data_state_reason}</EmptyState>
              </Panel>
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-ink-2">
                  <span>
                    Snapshot <strong className="text-ink">{s.period}</strong> revision {s.revision}
                  </span>
                  <span>Refreshed {fmtDateTime(s.created_at)}</span>
                  <span>Metrics as of {fmtDate(s.metrics_as_of)}</span>
                  <span>Next scheduled refresh {fmtDateTime(t.next_refresh)}</span>
                  {s.prices_moved_since_snapshot && <Badge tone="info">Prices have moved since this snapshot</Badge>}
                  {snapId && (
                    <Button variant="quiet" onClick={() => setSnapId(undefined)}>
                      Back to latest
                    </Button>
                  )}
                </div>
                <Tabs label="Theme sections" value={tab} onChange={setTab} tabs={[{ id: 'members', label: `Members (${s.constituents.length})` }, { id: 'method', label: 'Methodology' }, { id: 'excluded', label: `Excluded (${s.exclusions.length})` }, { id: 'history', label: 'Snapshot history' }]} />
                <Panel className="mt-3">
                  {tab === 'members' &&
                    (s.constituents.length === 0 ? (
                      <EmptyState title="No instrument qualifies">
                        {s.eligible_count} of {s.universe_count} instruments passed the filters at this snapshot. See the Excluded tab for reasons.
                      </EmptyState>
                    ) : (
                      <div className="scroll-x">
                        <table className="text-[13px]">
                          <thead>
                            <tr>
                              <th className="r">Rank</th>
                              <th>Instrument</th>
                              <th className="r">Theme score</th>
                              {valueKeys.map((k) => (
                                <th key={k} className="r">
                                  {labels[k]?.label ?? k}
                                </th>
                              ))}
                              <th className="r">Price at snapshot</th>
                              <th className="r">Since snapshot</th>
                              <th>Labels</th>
                            </tr>
                          </thead>
                          <tbody>
                            {s.constituents.map((c: any) => (
                              <tr key={c.instrument_id}>
                                <td className="r num">{c.rank}</td>
                                <td>
                                  <Link to={instrumentPath(c.symbol)} className="font-semibold">
                                    {c.symbol}
                                  </Link>
                                  <div className="text-xs text-ink-2">{c.sector}</div>
                                </td>
                                <td className="r num">{c.score == null ? <Missing /> : fmtNum(c.score, 0)}</td>
                                {valueKeys.map((k) => (
                                  <td key={k} className="r num">
                                    {c.values[k] == null ? <Missing /> : fmtMetric(k, c.values[k], labels[k]?.unit)}
                                  </td>
                                ))}
                                <td className="r num">{c.price_at_snapshot == null ? <Missing /> : `₹${fmtNum(c.price_at_snapshot)}`}</td>
                                <td className={`r num ${signClass(c.change_since_snapshot_pct)}`}>{fmtSigned(c.change_since_snapshot_pct)}</td>
                                <td>
                                  <div className="flex flex-wrap gap-1">
                                    {c.stance && <StateBadge state={c.stance} label={c.stance} />}
                                    {c.risk_label && <RiskBadge label={c.risk_label} />}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                  {tab === 'method' && (
                    <div className="space-y-3 px-4 py-4">
                      <ul className="m-0 list-disc space-y-1 pl-5">
                        {t.interpreted_filters.map((f: string) => (
                          <li key={f}>{f}</li>
                        ))}
                      </ul>
                      <p className="m-0 text-[13px] text-ink-2">
                        Theme score is the equal-weight average of the percentile ranks of the ranking metrics among instruments that pass every filter. An instrument with a missing filter metric is excluded, with the reason recorded. Definition version {s.theme_version};
                        formula versions {Object.entries(s.versions).map(([k, v]) => `${k} ${v}`).join(', ')}.
                      </p>
                      <p className="m-0 text-[13px] text-ink-2">A theme is a research screen, not a recommendation, and past membership says nothing certain about future returns.</p>
                    </div>
                  )}
                  {tab === 'excluded' &&
                    (s.exclusions.length === 0 ? (
                      <EmptyState title="Nothing was excluded" />
                    ) : (
                      <div className="scroll-x">
                        <table className="text-[13px]">
                          <thead>
                            <tr>
                              <th>Instrument</th>
                              <th>Reason</th>
                            </tr>
                          </thead>
                          <tbody>
                            {s.exclusions.map((e: any) => (
                              <tr key={e.symbol}>
                                <td className="font-medium">{e.symbol}</td>
                                <td className="text-ink-2">{e.reason}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                  {tab === 'history' && (
                    <Async q={hist}>
                      {(h) => (
                        <table className="text-[13px]">
                          <thead>
                            <tr>
                              <th>Period</th>
                              <th>Created</th>
                              <th>Trigger</th>
                              <th className="r">Members</th>
                              <th>Top five</th>
                              <th />
                            </tr>
                          </thead>
                          <tbody>
                            {h.items.map((r: any) => (
                              <tr key={r.id} className={r.id === s.id ? 'bg-accent-soft' : ''}>
                                <td className="font-medium">
                                  {r.period} r{r.revision}
                                </td>
                                <td>{fmtDateTime(r.created_at)}</td>
                                <td className="text-ink-2">{r.trigger}</td>
                                <td className="r num">{r.constituents}</td>
                                <td className="text-ink-2">{r.top.join(', ') || '—'}</td>
                                <td className="r">
                                  {r.id === s.id ? (
                                    <span className="text-ink-2">Shown</span>
                                  ) : (
                                    <Button variant="quiet" onClick={() => (setSnapId(r.id), setTab('members'))}>
                                      View
                                    </Button>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </Async>
                  )}
                </Panel>
              </>
            )}
            <Confirm open={confirmArchive} title="Archive this theme?" body="The theme is hidden from the list. Its snapshots are kept." confirmLabel="Archive theme" danger onConfirm={archive} onClose={() => setConfirmArchive(false)} />
          </>
        )
      }}
    </Async>
  )
}

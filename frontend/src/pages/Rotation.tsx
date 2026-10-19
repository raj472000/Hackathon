import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ApiError, post, useApi, waitForJob } from '../api/client'
import { RotationSchema } from '../api/schemas'
import { RRGChart } from '../components/Charts'
import { Async, Badge, Button, Dialog, EmptyState, Missing, Notice, PageHeader, Panel, RefreshButton, cx } from '../components/ui'
import { fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'

const QUAD_NOTE: Record<string, string> = {
  Leading: 'Stronger than the benchmark, and that strength is rising',
  Weakening: 'Stronger than the benchmark, but that strength is fading',
  Lagging: 'Weaker than the benchmark, and still losing ground',
  Improving: 'Weaker than the benchmark, but recovering',
}

const WEEK_OPTIONS = [4, 6, 8, 12, 16, 20, 26]

export function Rotation() {
  const qc = useQueryClient()
  const [trail, setTrail] = useState<number | ''>('')
  const [ratio, setRatio] = useState<number | ''>('')
  const [end, setEnd] = useState('')
  const [snapId, setSnapId] = useState<number | ''>('')
  const [compareId, setCompareId] = useState<number | ''>('')
  const [selected, setSelected] = useState<string[]>([])
  const [hidden, setHidden] = useState<string[]>([])
  const [help, setHelp] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const params = { trail: trail || undefined, ratio_window: ratio || undefined, end: end || undefined, snapshot_id: !trail && !ratio && !end ? snapId || undefined : undefined }
  const q = useApi<any>(['rotation'], '/sector-rotation', params, { schema: RotationSchema as any, placeholderData: (p: any) => p })
  const snaps = useApi<any>(['rotation-snapshots'], '/sector-rotation/snapshots')
  const cmp = useApi<any>(['rotation-compare'], compareId ? '/sector-rotation' : null, { snapshot_id: compareId })
  const toggle = (n: string) => setSelected((s) => (s.includes(n) ? s.filter((x) => x !== n) : s.length >= 3 ? [...s.slice(1), n] : [...s, n]))
  const refresh = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const { job_id } = await post('/sector-rotation/refresh')
      const j = await waitForJob(job_id)
      setMsg(j.status === 'succeeded' ? { tone: 'good', text: `Snapshot for slot ${j.detail.slot} ${j.detail.created ? 'created' : 'updated'} from data to ${fmtDate(j.detail.data_as_of)}.` } : { tone: 'bad', text: `Refresh ${j.status}: ${j.error}. The last valid snapshot is still shown.` })
      qc.invalidateQueries({ queryKey: ['rotation'] })
      qc.invalidateQueries({ queryKey: ['rotation-snapshots'] })
    } catch (e) {
      setMsg({ tone: 'bad', text: e instanceof ApiError ? e.message : 'Refresh failed.' })
    } finally {
      setBusy(false)
    }
  }
  const names = useMemo(() => Object.keys(q.data?.sectors ?? {}).sort(), [q.data])
  const visible = useMemo(() => new Set(names.filter((n) => !hidden.includes(n))), [names, hidden])
  return (
    <>
      <PageHeader
        title="Sector rotation"
        lede="A relative rotation graph: how each sector's strength against the benchmark, and the direction of that strength, has moved week by week."
        actions={
          <>
            <Button variant="quiet" onClick={() => setHelp(true)}>
              How to read this
            </Button>
            <Button onClick={refresh} busy={busy} title="Recompute the rotation graph from the latest stored prices">
              Recalculate
            </Button>
            <RefreshButton keys={[['rotation'], ['rotation-snapshots'], ['rotation-compare']]} />
          </>
        }
      />
      {msg && (
        <div className="mb-3">
          <Notice tone={msg.tone}>{msg.text}</Notice>
        </div>
      )}
      <Async q={q} rows={14}>
        {(d) =>
          d.data_state === 'missing' ? (
            <Panel>
              <EmptyState title="No rotation snapshot yet" action={<Button onClick={refresh} busy={busy}>Compute now</Button>}>
                {d.data_state_reason}
              </EmptyState>
            </Panel>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-ink-2">
                <span>
                  Benchmark <strong className="text-ink">{d.benchmark}</strong>
                  {d.benchmark_kind !== 'index' && ' (composite)'}
                </span>
                <span>Data to {fmtDate(d.data_as_of)}</span>
                <span>{d.on_demand ? 'Computed on demand, not stored' : `Snapshot computed ${fmtDateTime(d.computed_at)}`}</span>
                <span>
                  Lookback {d.params.ratio_window} weeks, momentum {d.params.momentum_window} weeks, trail {d.params.trail} weeks
                </span>
                <span>
                  {d.coverage.sectors_plotted} of {d.coverage.sectors_total} sectors plotted
                </span>
                {d.stale && <Badge tone="warn">Stale: older than 12 hours</Badge>}
              </div>
              {d.last_refresh_failed && (
                <Notice tone="bad" title="The latest refresh failed.">
                  {d.last_refresh_failed.note} Error at {fmtDateTime(d.last_refresh_failed.at)}: {d.last_refresh_failed.error}
                </Notice>
              )}
              {d.flags?.length > 0 && (
                <Notice tone="warn" title="Data notes.">
                  <ul className="m-0 mt-1 list-disc pl-5">
                    {d.flags.slice(0, 8).map((f: string) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                </Notice>
              )}
              <Panel>
                <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
                  <label className="block text-[13px]">
                    <span className="mb-1 block text-ink-2">Weeks computed</span>
                    <select className="field" value={trail} onChange={(e) => setTrail(e.target.value ? Number(e.target.value) : '')}>
                      <option value="">Stored (10 weeks)</option>
                      {WEEK_OPTIONS.map((n) => (
                        <option key={n} value={n}>
                          {n} weeks
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-[13px]">
                    <span className="mb-1 block text-ink-2">Lookback</span>
                    <select className="field" value={ratio} onChange={(e) => setRatio(e.target.value ? Number(e.target.value) : '')}>
                      <option value="">Stored (12 weeks)</option>
                      {[8, 10, 16, 26].map((n) => (
                        <option key={n} value={n}>
                          {n} weeks
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-[13px]">
                    <span className="mb-1 block text-ink-2">As of date</span>
                    <input type="date" className="field" value={end} max={d.benchmark_last_bar} onChange={(e) => setEnd(e.target.value)} />
                  </label>
                  <label className="block text-[13px]">
                    <span className="mb-1 block text-ink-2">Stored snapshot</span>
                    <select className="field" value={snapId} disabled={Boolean(trail || ratio || end)} onChange={(e) => setSnapId(e.target.value ? Number(e.target.value) : '')}>
                      <option value="">Latest</option>
                      {(snaps.data?.items ?? []).map((s: any) => (
                        <option key={s.snapshot_id} value={s.snapshot_id}>
                          {fmtDateTime(s.computed_at)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-[13px]">
                    <span className="mb-1 block text-ink-2">Compare quadrants with</span>
                    <select className="field" value={compareId} onChange={(e) => setCompareId(e.target.value ? Number(e.target.value) : '')}>
                      <option value="">None</option>
                      {(snaps.data?.items ?? []).map((s: any) => (
                        <option key={s.snapshot_id} value={s.snapshot_id}>
                          {fmtDateTime(s.computed_at)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {(trail || ratio || end || snapId) && (
                    <Button variant="quiet" onClick={() => (setTrail(''), setRatio(''), setEnd(''), setSnapId(''))}>
                      Reset view
                    </Button>
                  )}
                </div>
                <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 px-4 pt-3">
                  <legend className="sr-only">Sectors shown</legend>
                  {names.map((n) => {
                    const on = !hidden.includes(n)
                    return (
                      <button key={n} aria-pressed={on} onClick={() => setHidden(on ? [...hidden, n] : hidden.filter((x) => x !== n))} className={cx('rounded border px-2 py-0.5 text-xs', on ? 'border-rule-strong bg-surface' : 'border-rule bg-surface-2 text-ink-3 line-through')}>
                        {n}
                      </button>
                    )
                  })}
                </fieldset>
                <RRGChart
                  sectors={d.sectors}
                  selected={selected}
                  onToggle={toggle}
                  visible={visible}
                  onNeedMoreHistory={(trail || d.params.trail) < 26 ? () => setTrail(WEEK_OPTIONS.find((n) => n > (trail || d.params.trail)) ?? 26) : undefined}
                />
              </Panel>

              <Panel title="Sector values" aside={<span>Returns are price returns on {d.price_basis}</span>}>
                <div className="scroll-x">
                  <table className="text-[13px]">
                    <caption className="sr-only">The same data as the chart: latest RS-Ratio and RS-Momentum per sector with returns.</caption>
                    <thead>
                      <tr>
                        <th>Sector</th>
                        <th>Quadrant</th>
                        {compareId && <th>Earlier quadrant</th>}
                        <th className="r">RS-Ratio</th>
                        <th className="r">RS-Momentum</th>
                        <th>Heading</th>
                        <th className="r">1M</th>
                        <th className="r">3M</th>
                        <th className="r">6M</th>
                        <th className="r">12M</th>
                        <th>Series</th>
                      </tr>
                    </thead>
                    <tbody>
                      {names.map((n) => {
                        const s = d.sectors[n]
                        const head = s.trail?.[s.trail.length - 1]
                        const was = cmp.data?.sectors?.[n]?.quadrant
                        return (
                          <tr key={n} className={selected.includes(n) ? 'bg-accent-soft' : ''}>
                            <td>
                              <button className="text-left font-medium" onClick={() => toggle(n)} aria-pressed={selected.includes(n)}>
                                {n}
                              </button>
                            </td>
                            <td title={QUAD_NOTE[s.quadrant] ?? ''}>{s.quadrant ?? <Missing status={s.status} reason={s.reason} />}</td>
                            {compareId && <td className={was && was !== s.quadrant ? 'font-semibold' : 'text-ink-2'}>{was ?? '—'}{was && was !== s.quadrant ? ' (changed)' : ''}</td>}
                            <td className="r num">{head ? fmtNum(head.rs_ratio) : <Missing status={s.status} reason={s.reason} />}</td>
                            <td className="r num">{head ? fmtNum(head.rs_momentum) : <Missing status={s.status} reason={s.reason} />}</td>
                            <td className="text-ink-2">{s.direction ?? '—'}</td>
                            {['1M', '3M', '6M', '12M'].map((k) => (
                              <td key={k} className={`r num ${signClass(s.returns?.[k])}`}>
                                {fmtSigned(s.returns?.[k])}
                              </td>
                            ))}
                            <td className="text-ink-2" title={s.series_note}>
                              {s.series_kind === 'index' ? `Index ${s.index_symbol}` : s.series_kind === 'composite' ? `Composite of ${s.members}` : 'Unavailable'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </div>
          )
        }
      </Async>
      <Dialog open={help} title="How to read the rotation graph" onClose={() => setHelp(false)}>
        <div className="space-y-3">
          <p className="m-0">
            <strong>Horizontal axis, RS-Ratio.</strong> The sector's price divided by the benchmark's, compared with its own average over the lookback. Above 100 means the sector has been stronger than the benchmark.
          </p>
          <p className="m-0">
            <strong>Vertical axis, RS-Momentum.</strong> Whether that relative strength is rising (above 100) or falling (below 100).
          </p>
          <p className="m-0">
            <strong>Centre lines.</strong> Both cross at 100, which splits the chart into four quadrants.
          </p>
          <dl className="m-0 space-y-1">
            {Object.entries(QUAD_NOTE).map(([k, v]) => (
              <div key={k}>
                <dt className="inline font-semibold">{k}: </dt>
                <dd className="m-0 inline text-ink-2">{v}.</dd>
              </div>
            ))}
          </dl>
          <p className="m-0">
            <strong>Movement.</strong> Each line shows the last weeks, ending at the labelled dot. Sectors often travel clockwise, but they do not have to.
          </p>
          <pre className="m-0 whitespace-pre-wrap rounded bg-surface-2 p-3 text-xs text-ink-2">{q.data?.method}</pre>
          <p className="m-0 text-[13px] text-ink-2">{q.data?.interpretation} This is this project's own documented method; it is not a reproduction of any vendor's proprietary calculation.</p>
        </div>
      </Dialog>
    </>
  )
}

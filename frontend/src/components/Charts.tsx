import { CandlestickSeries, HistogramSeries, LineSeries, createChart, type IChartApi } from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtDate, fmtNum } from '../lib/format'
import { Button, cx } from './ui'

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export type Bar = { date: string; open: number | null; high: number | null; low: number | null; close: number; volume: number | null; provider: string }

/** Daily price chart. Gain/loss bodies are also distinguishable by the hollow/filled convention in the legend row. */
export function PriceChart({ bars, height = 340 }: { bars: Bar[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null)
  const [mode, setMode] = useState<'candles' | 'line'>('candles')
  const [hover, setHover] = useState<Bar | null>(null)
  const last = bars[bars.length - 1]
  useEffect(() => {
    if (!el.current || bars.length === 0) return
    const ink2 = cssVar('--ink-2')
    const rule = cssVar('--rule')
    const chart: IChartApi = createChart(el.current, {
      height,
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: ink2, fontFamily: cssVar('--font-body'), attributionLogo: true },
      grid: { vertLines: { visible: false }, horzLines: { color: rule } },
      rightPriceScale: { borderColor: rule },
      timeScale: { borderColor: rule, rightOffset: 4 },
      crosshair: { mode: 1 },
    })
    const gain = cssVar('--gain')
    const loss = cssVar('--loss')
    const usable = bars.filter((b) => b.open != null && b.high != null && b.low != null)
    const main =
      mode === 'candles' && usable.length === bars.length
        ? chart.addSeries(CandlestickSeries, { upColor: gain, downColor: loss, borderUpColor: gain, borderDownColor: loss, wickUpColor: gain, wickDownColor: loss })
        : chart.addSeries(LineSeries, { color: cssVar('--glyph'), lineWidth: 2, priceLineVisible: false })
    main.setData(
      (mode === 'candles' && usable.length === bars.length
        ? bars.map((b) => ({ time: b.date, open: b.open as number, high: b.high as number, low: b.low as number, close: b.close }))
        : bars.map((b) => ({ time: b.date, value: b.close }))) as any,
    )
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: cssVar('--glyph-empty') })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } })
    vol.setData(bars.filter((b) => b.volume != null).map((b) => ({ time: b.date, value: b.volume as number })) as any)
    const byDate = new Map(bars.map((b) => [b.date, b]))
    chart.subscribeCrosshairMove((p) => setHover(p.time ? (byDate.get(String(p.time)) ?? null) : null))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [bars, mode, height])
  const b = hover ?? last
  if (!b) return null
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3 text-[13px]">
        <div className="num flex flex-wrap gap-x-4 gap-y-0.5 text-ink-2" aria-live="off">
          <span className="font-medium text-ink">{fmtDate(b.date)}</span>
          <span>O {fmtNum(b.open)}</span>
          <span>H {fmtNum(b.high)}</span>
          <span>L {fmtNum(b.low)}</span>
          <span>
            C <strong className="text-ink">{fmtNum(b.close)}</strong>
          </span>
          <span>Vol {b.volume == null ? '—' : fmtNum(b.volume, 0)}</span>
          <span>Source {b.provider === 'nse_mcp' ? 'NSE MCP' : b.provider === 'kite_mcp' ? 'Kite MCP' : b.provider}</span>
        </div>
        <div className="flex gap-1" role="group" aria-label="Chart type">
          {(['candles', 'line'] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m} className={cx('rounded border px-2 py-1 text-xs', mode === m ? 'border-accent bg-accent-soft font-semibold' : 'border-rule-strong')}>
              {m === 'candles' ? 'Candles' : 'Line'}
            </button>
          ))}
        </div>
      </div>
      <div ref={el} style={{ height }} className="px-2" role="img" aria-label={`Daily price chart, ${bars.length} bars from ${bars[0].date} to ${last.date}. Latest close ${fmtNum(last.close)} rupees.`} />
    </div>
  )
}

export function Sparkline({ values, width = 160, height = 40, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length < 2) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * (width - 6) + 3},${height - 4 - ((v - min) / (max - min || 1)) * (height - 8)}`)
  const [lx, ly] = pts[pts.length - 1].split(',').map(Number)
  return (
    <svg width={width} height={height} role="img" aria-label={label}>
      <polyline points={pts.join(' ')} fill="none" stroke="var(--glyph)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={4} fill="var(--glyph)" stroke="var(--surface)" strokeWidth={2} />
    </svg>
  )
}

// ------------------------------------------------------------------ Relative Rotation Graph
type Pt = { date: string; rs_ratio: number; rs_momentum: number }
export type RRGSector = { status: string; quadrant?: string | null; trail?: Pt[]; direction?: string | null }
const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']
const QUADS = [
  { name: 'Improving', x: 0, y: 0, fill: 'var(--quad-improving)', ax: 'start', ay: 'top' },
  { name: 'Leading', x: 1, y: 0, fill: 'var(--quad-leading)', ax: 'end', ay: 'top' },
  { name: 'Lagging', x: 0, y: 1, fill: 'var(--quad-lagging)', ax: 'start', ay: 'bottom' },
  { name: 'Weakening', x: 1, y: 1, fill: 'var(--quad-weakening)', ax: 'end', ay: 'bottom' },
] as const

/**
 * Sectors are identified by direct labels, not colour: ten or more sectors cannot be told apart by
 * hue safely. Up to three selected sectors take the validated highlight colours; the rest stay ink.
 */
export const DEFAULT_TAIL_WEEKS = 2

export function RRGChart({ sectors, selected, onToggle, visible, onNeedMoreHistory }: { sectors: Record<string, RRGSector>; selected: string[]; onToggle: (name: string) => void; visible: Set<string>; onNeedMoreHistory?: () => void }) {
  const [zoom, setZoom] = useState(1)
  const [hover, setHover] = useState<{ name: string; pt: Pt; x: number; y: number } | null>(null)
  const maxTail = useMemo(() => Math.max(1, ...Object.values(sectors).map((s) => s.trail?.length ?? 0)), [sectors])
  const [tail, setTail] = useState(DEFAULT_TAIL_WEEKS)
  const [back, setBack] = useState(0) // weeks stepped back from the latest observation
  const maxBack = Math.max(0, maxTail - 1)
  const offset = Math.min(back, maxBack)
  const shown = Math.min(tail, maxTail - offset)
  // `shown` weekly points ending `offset` weeks before the latest are drawn; the axes rescale to what is drawn.
  const trailOf = (n: string) => {
    const all = (sectors[n].trail ?? []) as Pt[]
    const end = Math.max(1, all.length - offset)
    return all.slice(Math.max(0, end - shown), end)
  }
  const anyName = Object.keys(sectors).find((n) => (sectors[n].trail?.length ?? 0) === maxTail)
  const headDate = anyName ? trailOf(anyName).at(-1)?.date : undefined
  const W = 760
  const H = 520
  const M = { l: 52, r: 18, t: 16, b: 44 }
  const names = useMemo(() => Object.keys(sectors).filter((n) => sectors[n].status === 'ok' && (sectors[n].trail?.length ?? 0) > 0 && visible.has(n)).sort(), [sectors, visible])
  const span = useMemo(() => {
    let m = 1.5
    for (const n of names) for (const p of trailOf(n)) m = Math.max(m, Math.abs(p.rs_ratio - 100), Math.abs(p.rs_momentum - 100))
    return (m * 1.18) / zoom
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names, sectors, zoom, shown, offset])
  const sx = (v: number) => M.l + ((v - (100 - span)) / (2 * span)) * (W - M.l - M.r)
  const sy = (v: number) => M.t + (1 - (v - (100 - span)) / (2 * span)) * (H - M.t - M.b)
  const cx0 = sx(100)
  const cy0 = sy(100)
  const ticks = useMemo(() => {
    const step = [0.5, 1, 2, 5, 10].find((s) => (2 * span) / s <= 8) ?? 20
    const out: number[] = []
    for (let v = Math.ceil((100 - span) / step) * step; v <= 100 + span; v += step) out.push(Math.round(v * 10) / 10)
    return out
  }, [span])
  // Direct labels at each head, nudged apart vertically so they do not collide.
  const labels = useMemo(() => {
    const ls = names.map((n) => {
      const p = trailOf(n)[trailOf(n).length - 1]
      return { n, x: sx(p.rs_ratio), y: sy(p.rs_momentum) }
    })
    ls.sort((a, b) => a.y - b.y)
    for (let i = 1; i < ls.length; i++) {
      for (let j = 0; j < i; j++) if (Math.abs(ls[i].x - ls[j].x) < 120 && ls[i].y - ls[j].y < 14 && ls[i].y >= ls[j].y) ls[i].y = ls[j].y + 14
    }
    return new Map(ls.map((l) => [l.n, l]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names, sectors, span, shown, offset])
  const colorOf = (n: string) => {
    const i = selected.indexOf(n)
    return i >= 0 ? SERIES[i] : 'var(--ink)'
  }
  const focus = hover?.name
  return (
    <div className="relative">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
        <p className="m-0 text-[13px] text-ink-2">Each line is one sector's weekly path; the labelled dot is the latest week. Select up to three sectors to highlight them.</p>
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2" role="group" aria-label="Tail length">
          <span>Tail</span>
          <Button onClick={() => setTail(Math.max(1, shown - 1))} disabled={shown <= 1} aria-label="Shorter tail" data-testid="rrg-tail-less">
            −
          </Button>
          <input type="range" min={1} max={Math.max(1, maxTail - offset)} step={1} value={shown} onChange={(e) => setTail(Number(e.target.value))} aria-label="Tail length in weeks" data-testid="rrg-tail" className="w-24 accent-[var(--accent)]" />
          <Button onClick={() => setTail(Math.min(maxTail - offset, shown + 1))} disabled={shown >= maxTail - offset} aria-label="Longer tail" data-testid="rrg-tail-more">
            +
          </Button>
          <span className="num w-16 text-ink" aria-live="polite">
            {shown} {shown === 1 ? 'week' : 'weeks'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2" role="group" aria-label="Step through weeks">
          <Button onClick={() => setBack(offset + 1)} disabled={offset >= maxBack} aria-label="One week earlier" data-testid="rrg-back">
            ◀ Week
          </Button>
          <Button onClick={() => setBack(Math.max(0, offset - 1))} disabled={offset === 0} aria-label="One week later" data-testid="rrg-forward">
            Week ▶
          </Button>
          <Button variant="quiet" onClick={() => setBack(0)} disabled={offset === 0}>
            Latest
          </Button>
          <span className="text-ink" aria-live="polite" data-testid="rrg-as-of">
            {offset === 0 ? 'Latest week' : `${offset} ${offset === 1 ? 'week' : 'weeks'} back`}
            {headDate && ` · week ending ${headDate}`}
          </span>
          {offset >= maxBack && maxBack > 0 && onNeedMoreHistory && (
            <button type="button" className="text-link underline-offset-2 hover:underline" onClick={onNeedMoreHistory} data-testid="rrg-more-history">
              Earliest computed week · load more weeks
            </button>
          )}
        </div>
        <div className="flex gap-1" role="group" aria-label="Zoom">
          <Button onClick={() => setZoom((z) => Math.min(z * 1.4, 6))} aria-label="Zoom in">
            +
          </Button>
          <Button onClick={() => setZoom((z) => Math.max(z / 1.4, 0.5))} aria-label="Zoom out">
            −
          </Button>
          <Button onClick={() => setZoom(1)}>Reset</Button>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label={`Relative rotation graph of ${names.length} sectors. A table of the same values follows.`} onMouseLeave={() => setHover(null)}>
        <defs>
          <clipPath id="rrg-clip">
            <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} />
          </clipPath>
        </defs>
        {QUADS.map((q) => (
          <g key={q.name}>
            <rect x={q.x ? cx0 : M.l} y={q.y ? cy0 : M.t} width={q.x ? W - M.r - cx0 : cx0 - M.l} height={q.y ? H - M.b - cy0 : cy0 - M.t} fill={q.fill} />
            <text x={q.ax === 'start' ? M.l + 10 : W - M.r - 10} y={q.ay === 'top' ? M.t + 20 : H - M.b - 10} textAnchor={q.ax} fontSize={13} fontWeight={600} fill="var(--ink-2)">
              {q.name}
            </text>
          </g>
        ))}
        {ticks.map((t) => (
          <g key={t} fontSize={11} fill="var(--ink-2)">
            <text x={sx(t)} y={H - M.b + 16} textAnchor="middle">
              {t}
            </text>
            <text x={M.l - 8} y={sy(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        <line x1={cx0} x2={cx0} y1={M.t} y2={H - M.b} stroke="var(--rule-strong)" />
        <line x1={M.l} x2={W - M.r} y1={cy0} y2={cy0} stroke="var(--rule-strong)" />
        <text x={(M.l + W - M.r) / 2} y={H - 8} textAnchor="middle" fontSize={12} fill="var(--ink-2)">
          RS-Ratio: relative strength against the benchmark (100 = in line)
        </text>
        <text transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" fontSize={12} fill="var(--ink-2)">
          RS-Momentum: is relative strength rising or falling (100 = flat)
        </text>
        <g clipPath="url(#rrg-clip)">
          {names.map((n) => {
            const trail = trailOf(n)
            const col = colorOf(n)
            const dim = (focus && focus !== n) || (selected.length > 0 && !selected.includes(n) && focus !== n)
            const head = trail[trail.length - 1]
            return (
              <g key={n} opacity={dim ? 0.28 : 1}>
                <polyline points={trail.map((p) => `${sx(p.rs_ratio)},${sy(p.rs_momentum)}`).join(' ')} fill="none" stroke={col} strokeWidth={selected.includes(n) ? 2.5 : 1.5} strokeLinejoin="round" strokeLinecap="round" opacity={selected.includes(n) ? 0.95 : 0.5} />
                {trail.slice(0, -1).map((p, i) => (
                  <circle key={p.date} cx={sx(p.rs_ratio)} cy={sy(p.rs_momentum)} r={2.2} fill={col} opacity={0.35 + (0.5 * i) / trail.length} />
                ))}
                <circle cx={sx(head.rs_ratio)} cy={sy(head.rs_momentum)} r={5.5} fill={col} stroke="var(--surface)" strokeWidth={2} />
                {trail.map((p) => (
                  <circle
                    key={`h${p.date}`}
                    cx={sx(p.rs_ratio)}
                    cy={sy(p.rs_momentum)}
                    r={11}
                    fill="transparent"
                    style={{ cursor: 'pointer' }}
                    onMouseEnter={() => setHover({ name: n, pt: p, x: sx(p.rs_ratio), y: sy(p.rs_momentum) })}
                    onClick={() => onToggle(n)}
                  />
                ))}
              </g>
            )
          })}
        </g>
        {names.map((n) => {
          const l = labels.get(n)
          if (!l) return null
          const right = l.x < W - 190
          const dim = (focus && focus !== n) || (selected.length > 0 && !selected.includes(n) && focus !== n)
          return (
            <text key={`l${n}`} x={l.x + (right ? 10 : -10)} y={l.y + 4} textAnchor={right ? 'start' : 'end'} fontSize={12} fontWeight={selected.includes(n) || focus === n ? 700 : 500} fill="var(--ink)" opacity={dim ? 0.35 : 1} stroke="var(--surface)" strokeWidth={3} paintOrder="stroke" style={{ cursor: 'pointer' }} onClick={() => onToggle(n)}>
              {n}
            </text>
          )
        })}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute z-10 rounded-md border border-rule-strong bg-surface px-3 py-2 text-[12px] shadow-md" style={{ left: `min(${(hover.x / W) * 100}%, calc(100% - 210px))`, top: `calc(${(hover.y / H) * 100}% + 56px)` }}>
          <div className="font-semibold">{hover.name}</div>
          <div className="text-ink-2">Week ending {fmtDate(hover.pt.date)}</div>
          <div className="num">RS-Ratio {fmtNum(hover.pt.rs_ratio)}</div>
          <div className="num">RS-Momentum {fmtNum(hover.pt.rs_momentum)}</div>
        </div>
      )}
      {selected.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 px-4 pb-2 text-[13px]" aria-label="Highlighted sectors">
          {selected.map((n, i) => (
            <li key={n} className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: SERIES[i] }} aria-hidden />
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

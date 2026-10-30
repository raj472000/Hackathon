import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../api/client'
import { fmtDateTime, fmtNum } from '../lib/format'
import { cx } from './ui'

type Item = { symbol: string; last_price: number | null; change_pct: number | null; source: string | null; as_of?: string | null }

function Change({ v, dark }: { v: number | null; dark?: boolean }) {
  if (v == null) return <span className={dark ? 'text-rail-ink-2' : 'text-ink-3'}>—</span>
  const up = v > 0
  const down = v < 0
  const tone = dark ? (up ? 'text-[var(--gain-on-dark)]' : down ? 'text-[var(--loss-on-dark)]' : 'text-rail-ink-2') : up ? 'text-gain' : down ? 'text-loss' : 'text-ink-2'
  return (
    <span className={cx('num', tone)}>
      <span aria-hidden>{up ? '▲' : down ? '▼' : '■'}</span> {fmtNum(Math.abs(v), 2)}%
      <span className="sr-only">{up ? ' up' : down ? ' down' : ' unchanged'}</span>
    </span>
  )
}

function Entry({ to, label, price, change, title, dark }: { to?: string; label: string; price: number | null; change: number | null; title?: string; dark?: boolean }) {
  const body = (
    <>
      <span className={cx('font-semibold', dark ? 'text-white' : 'text-ink')}>{label}</span>
      <span className={cx('num', dark ? 'text-rail-ink' : 'text-ink')}>{price == null ? '—' : fmtNum(price, 2)}</span>
      <Change v={change} dark={dark} />
    </>
  )
  const cls = 'inline-flex shrink-0 items-baseline gap-2 whitespace-nowrap px-3 text-[13px] no-underline'
  return to ? (
    <Link to={to} className={cls} title={title}>
      {body}
    </Link>
  ) : (
    <span className={cls} title={title}>
      {body}
    </span>
  )
}

const SPEED_PX_PER_S = 40

/** Nifty 50 constituents and the index, scrolling slowly; pauses on hover or keyboard focus and for reduced motion.
 *  The scroll is a CSS transform on the track, not scrollLeft: browsers round scrollLeft writes differently (Chrome,
 *  Edge and Firefox to whole pixels, Safari to device pixels), and the < 1 px a frame this ticker moves was lost to
 *  that rounding, so it stood still. A fractional translate3d moves the same way in every browser. */
export function Nifty50Ticker({ tone = 'light' }: { tone?: 'light' | 'dark' }) {
  const dark = tone === 'dark'
  const q = useApi<any>(['nifty50-ticker'], '/markets/nifty50', undefined, { refetchInterval: 60_000 })
  const viewport = useRef<HTMLDivElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const firstRow = useRef<HTMLDivElement>(null)
  const pos = useRef(0) // px the track is shifted left, kept within one copy of the list
  const pending = useRef(0) // px still to travel from the arrow buttons, eased in over a few frames
  const paused = useRef(false)
  const drag = useRef<{ x: number; pos: number; id: number } | null>(null)
  const dragged = useRef(false) // a drag ended on a link: swallow that click so it does not navigate
  const [reduced] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
  // Only stocks some source priced; a constituent with no price is left out rather than shown blank.
  const items: Item[] = (q.data?.items ?? []).filter((i: Item) => i.last_price != null)
  const animate = !reduced && items.length > 0

  useEffect(() => {
    if (!animate) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const dt = Math.min(now - last, 100) / 1000 // a long gap (background tab) does not jump
      last = now
      const w = firstRow.current?.offsetWidth ?? 0 // one copy of the list; the second copy makes the loop seamless
      if (w > 0 && track.current) {
        if (!drag.current) {
          const ease = pending.current * Math.min(1, dt * 10)
          pending.current -= ease
          if (Math.abs(pending.current) < 0.5) pending.current = 0
          pos.current += ease + (paused.current ? 0 : dt * SPEED_PX_PER_S)
        }
        pos.current = ((pos.current % w) + w) % w
        track.current.style.transform = `translate3d(${-pos.current}px, 0, 0)`
      }
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [animate])

  const nudge = (dir: number) => {
    if (animate) pending.current += dir * 320
    else viewport.current?.scrollBy({ left: dir * 320, behavior: 'smooth' })
  }
  // Swipe or drag to move the ticker on touch screens and with a mouse.
  const onPointerDown = (e: React.PointerEvent) => {
    if (!animate || e.button !== 0) return
    drag.current = { x: e.clientX, pos: pos.current, id: e.pointerId }
    dragged.current = false
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const g = drag.current
    if (!g || g.id !== e.pointerId) return
    if (!dragged.current && Math.abs(e.clientX - g.x) > 4) {
      dragged.current = true
      viewport.current?.setPointerCapture?.(e.pointerId)
    }
    pos.current = g.pos - (e.clientX - g.x)
  }
  const endDrag = (e: React.PointerEvent) => {
    if (drag.current?.id === e.pointerId) drag.current = null
  }
  if (q.isLoading)
    return dark ? <div className="h-10 border-y border-rail-2 bg-rail-2/40" aria-label="Loading Nifty 50 prices" role="status" /> : <div className="mb-4 h-10 rounded-[10px] border border-rule bg-surface skeleton" aria-label="Loading Nifty 50 prices" role="status" />
  if (q.error || !q.data || q.data.state === 'not_configured' || items.length === 0) {
    return q.data?.reason ? <p className={cx('text-xs', dark ? 'border-y border-rail-2 px-5 py-2.5 text-rail-ink-2 lg:px-8' : 'mb-4 text-ink-2')}>Nifty 50 ticker: {q.data.reason}</p> : null
  }
  const d = q.data
  const idx = d.index ?? {}
  const sources = [d.sources?.nse?.priced || d.sources?.nse_close?.priced ? 'NSE' : null, d.sources?.kite?.priced ? 'Zerodha Kite' : null].filter(Boolean).join(' and ')
  const row = (copy: number) => (
    <div ref={copy === 0 ? firstRow : undefined} className="flex shrink-0 items-center" aria-hidden={copy === 1 || undefined}>
      {items.map((i) => (
        <Entry key={`${copy}-${i.symbol}`} to={copy === 0 ? `/instrument/NSE:${encodeURIComponent(i.symbol)}` : undefined} label={i.symbol}
          price={i.last_price} change={i.change_pct} dark={dark} title={`${i.symbol}${i.source ? ` · ${i.source}` : ''}${i.as_of ? ` · ${fmtDateTime(i.as_of)}` : ''}`} />
      ))}
      {idx.value != null && <Entry label="NIFTY 50" price={idx.value} change={idx.change_pct ?? null} dark={dark} title={idx.source ? `Nifty 50 · ${idx.source}` : undefined} />}
    </div>
  )
  return (
    <section
      aria-label="Nifty 50 constituents"
      className={cx('flex items-center', dark ? 'border-y border-rail-2 bg-rail-2/40' : 'mb-4 rounded-[10px] border border-rule bg-surface')}
      onMouseEnter={() => (paused.current = true)}
      onMouseLeave={() => (paused.current = false)}
      onFocus={() => (paused.current = true)}
      onBlur={() => (paused.current = false)}
      data-testid="nifty50-ticker"
    >
      <button type="button" onClick={() => nudge(-1)} aria-label="Scroll Nifty 50 ticker left" className={cx('shrink-0 px-2.5 py-2', dark ? 'text-rail-ink-2 hover:text-white' : 'text-ink-2 hover:text-ink')}>
        ‹
      </button>
      <div ref={viewport} tabIndex={0}
        className={cx('relative min-w-0 flex-1 py-2', animate ? 'overflow-hidden touch-pan-y select-none' : 'overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden')}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}
        onClickCapture={(e) => {
          if (dragged.current) {
            dragged.current = false
            e.preventDefault()
            e.stopPropagation()
          }
        }}
        aria-label={`Nifty 50 prices, ${items.length} of ${d.total} priced from ${sources || 'no source'}`}>
        <div ref={track} className="flex w-max" style={animate ? { willChange: 'transform' } : undefined}>
          {row(0)}
          {animate && row(1)}
        </div>
      </div>
      <button type="button" onClick={() => nudge(1)} aria-label="Scroll Nifty 50 ticker right" className={cx('shrink-0 px-2.5 py-2', dark ? 'text-rail-ink-2 hover:text-white' : 'text-ink-2 hover:text-ink')}>
        ›
      </button>
    </section>
  )
}

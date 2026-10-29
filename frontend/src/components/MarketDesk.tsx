import { useEffect, useState, type ReactNode } from 'react'
import { useApi } from '../api/client'
import { fmtDateTime, fmtNum, fmtSigned } from '../lib/format'
import { fmtClock, istDateHeading, SESSION, sessionState, useNow, type Phase } from '../lib/session'
import { Nifty50Ticker } from './Nifty50Ticker'
import { cx, Missing } from './ui'

const PHASE_DOT: Record<Phase, string> = {
  open: 'bg-gain',
  pre_open: 'bg-accent',
  closing: 'bg-accent',
  closed: 'bg-ink-3',
}

/** Small session indicator for the top bar, so every page shows whether the market is trading. */
export function SessionChip() {
  const now = useNow()
  const s = sessionState(now)
  return (
    <span
      className="hidden items-center gap-1.5 whitespace-nowrap text-xs text-ink-2 sm:inline-flex"
      title={`${s.detail}. Regular NSE hours; exchange holidays are not checked.`}
    >
      <span aria-hidden className={cx('h-[7px] w-[7px] rounded-full', PHASE_DOT[s.phase])} />
      <span className="font-medium text-ink">{s.phase === 'open' ? 'Market open' : s.phase === 'closed' ? 'Market closed' : s.label}</span>
      <span className="num">{fmtClock(s.minutes)} IST</span>
    </span>
  )
}

type Idx = {
  key: string
  label: string
  exchange: string
  state: string
  value: number | null
  change?: number | null
  change_pct?: number | null
  as_of?: string | null
  source?: string
  reason?: string
  note?: string
}

const SPAN = SESSION.postClose - SESSION.preOpen
const pct = (min: number) => `${((Math.min(Math.max(min, SESSION.preOpen), SESSION.postClose) - SESSION.preOpen) / SPAN) * 100}%`

/** 09:00 to 16:00 IST: pre-open, regular trading and the closing session, with a marker for now. */
function SessionBand({ minutes, tradingDay }: { minutes: number; tradingDay: boolean }) {
  const inWindow = tradingDay && minutes >= SESSION.preOpen && minutes <= SESSION.postClose
  const elapsedEnd = !tradingDay || minutes < SESSION.open ? SESSION.open : Math.min(minutes, SESSION.close)
  const [drawn, setDrawn] = useState(false)
  useEffect(() => {
    const r = requestAnimationFrame(() => setDrawn(true))
    return () => cancelAnimationFrame(r)
  }, [])
  return (
    <div className="mt-6">
      <div className="relative h-[26px]" role="img" aria-label={`NSE session from 09:00 to 16:00 IST. ${inWindow ? `Now ${fmtClock(minutes)} IST.` : 'Outside session hours now.'}`}>
        <span className="absolute top-[9px] h-2 rounded-l-full bg-rail-ink-2/45" style={{ left: 0, width: `calc(${pct(SESSION.open)} - 2px)` }} title="Pre-open 09:00–09:15" />
        <span className="absolute top-[9px] h-2 bg-rail-ink-2/25" style={{ left: pct(SESSION.open), width: `calc(${pct(SESSION.close)} - ${pct(SESSION.open)} - 2px)` }} title="Regular trading 09:15–15:30" />
        <span className="absolute top-[9px] h-2 rounded-r-full bg-rail-ink-2/45" style={{ left: pct(SESSION.close), right: 0 }} title="Closing session 15:30–16:00" />
        <span
          className="session-elapsed absolute top-[9px] h-2 origin-left bg-rail-ink"
          style={{ left: pct(SESSION.open), width: `calc(${pct(elapsedEnd)} - ${pct(SESSION.open)})`, transform: drawn ? 'scaleX(1)' : 'scaleX(0)' }}
        />
        {inWindow && <span className="absolute top-0 h-[26px] w-[3px] -translate-x-1/2 rounded-full bg-accent" style={{ left: pct(minutes) }} />}
      </div>
      <div className="relative mt-1.5 h-4 whitespace-nowrap text-[11.5px] text-rail-ink-2">
        <span className="num absolute" style={{ left: pct(SESSION.open) }}>
          09:15 open
        </span>
        <span className="num absolute -translate-x-full" style={{ left: pct(SESSION.close) }}>
          15:30 close
        </span>
        <span className="num absolute right-0 hidden sm:inline">16:00</span>
        {inWindow && minutes > SESSION.open + 60 && minutes < SESSION.close - 75 && (
          <span className="num absolute font-semibold text-accent" style={{ left: `calc(${pct(minutes)} + 8px)` }}>
            {fmtClock(minutes)} now
          </span>
        )}
      </div>
    </div>
  )
}

function IndexFigure({ i }: { i: Idx }) {
  const up = (i.change ?? 0) > 0
  const down = (i.change ?? 0) < 0
  return (
    <div className="min-w-0 border-t border-rail-2 pt-3 first:border-t-0 first:pt-0 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-1 sm:first:border-l-0 sm:first:pl-0" data-testid={`index-${i.key}`}>
      <p className="m-0 text-[13px] font-medium text-rail-ink-2">
        {i.label} <span className="text-[11px] font-normal">{i.exchange}</span>
      </p>
      {i.value == null ? (
        <p className="mt-2 text-sm text-rail-ink">
          <Missing reason={i.reason} status={i.state} /> <span className="text-xs">{i.reason}</span>
        </p>
      ) : (
        <>
          <p className="num m-0 mt-0.5 font-display text-[28px] font-semibold leading-[1.15] tracking-[-0.02em] text-white sm:text-[30px] lg:text-[34px]">{fmtNum(i.value)}</p>
          <p className={cx('num m-0 text-[13.5px] font-semibold', up ? 'text-[var(--gain-on-dark)]' : down ? 'text-[var(--loss-on-dark)]' : 'text-rail-ink')}>
            {fmtSigned(i.change_pct, 2)}
            <span className="ml-2 font-medium">{i.change == null ? '' : `${i.change > 0 ? '+' : i.change < 0 ? '−' : ''}${fmtNum(Math.abs(i.change))}`}</span>
          </p>
          <p
            className="m-0 mt-1 truncate text-[11px] text-rail-ink-2"
            title={[i.source, i.state === 'delayed' ? 'delayed' : null, i.note].filter(Boolean).join(' · ') || undefined}
          >
            {i.as_of ? fmtDateTime(i.as_of) : 'Time not supplied'}
          </p>
        </>
      )}
    </div>
  )
}

/** Overview hero: today's date and session, the three headline indices and the Nifty 50 tape. */
export function MarketDesk({ actions }: { actions?: ReactNode }) {
  const now = useNow()
  const s = sessionState(now)
  const q = useApi<{ indices: Idx[] }>(['indices'], '/markets/indices', undefined, { refetchInterval: 60_000 })
  return (
    <section aria-labelledby="desk-date" className="overflow-hidden rounded-2xl border border-[color-mix(in_srgb,var(--rail-ink)_12%,transparent)] bg-rail text-rail-ink">
      <div className="px-5 pb-5 pt-6 lg:px-8 lg:pt-7">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div>
            <h1 className="sr-only">Overview</h1>
            <p id="desk-date" className="m-0 font-display text-[34px] font-semibold leading-[1.05] tracking-[-0.025em] text-white lg:text-[44px]">
              {istDateHeading(now)}
            </p>
            <p className="m-0 mt-2 max-w-[60ch] text-[15px]">
              <span aria-hidden className={cx('mr-2 inline-block h-2 w-2 rounded-full align-[1px]', s.phase === 'closed' ? 'bg-rail-ink-2' : 'bg-accent')} />
              <span className="font-medium text-white">{s.label}.</span> {s.detail}
            </p>
          </div>
          {actions && <div className="desk-actions flex items-center gap-2">{actions}</div>}
        </div>
      </div>
      <Nifty50Ticker tone="dark" />
      <div className="px-5 pt-5 lg:px-8">
        <div className="grid gap-3 sm:grid-cols-3 sm:gap-5" aria-label="Headline indices">
          {q.isLoading ? (
            [0, 1, 2].map((k) => (
              <div key={k} className="space-y-2 py-1" role="status" aria-label="Loading index">
                <div className="h-3 w-24 animate-pulse rounded bg-rail-2 motion-reduce:animate-none" />
                <div className="h-8 w-44 animate-pulse rounded bg-rail-2 motion-reduce:animate-none" />
                <div className="h-3 w-32 animate-pulse rounded bg-rail-2 motion-reduce:animate-none" />
              </div>
            ))
          ) : q.error || !q.data ? (
            <p className="text-sm text-[var(--loss-on-dark)] sm:col-span-3">Index values could not be loaded: {(q.error as Error)?.message}</p>
          ) : (
            q.data.indices.map((i) => <IndexFigure key={i.key} i={i} />)
          )}
        </div>
        <SessionBand minutes={s.minutes} tradingDay={s.tradingDay} />
      </div>
      <p className="m-0 px-5 pb-4 pt-3 text-[11px] text-rail-ink-2 lg:px-8">Session band uses regular NSE hours in IST. Exchange holidays are not checked.</p>
    </section>
  )
}

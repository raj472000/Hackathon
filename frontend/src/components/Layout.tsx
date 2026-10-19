import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useApi } from '../api/client'
import { SourcesSchema, type Sources } from '../api/schemas'
import { ago, fmtDate } from '../lib/format'
import { InstrumentSearch } from './InstrumentSearch'
import { cx, stateTone } from './ui'
import { BrokerConnectButton, BrokerLoginProvider } from './BrokerLogin'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/screener', label: 'Screener' },
  { to: '/themes', label: 'Themes' },
  { to: '/rotation', label: 'Sector rotation' },
  { to: '/research', label: 'Research board' },
  { to: '/concalls', label: 'Concall follow-through' },
  { to: '/watchlists', label: 'Watchlists and alerts' },
  { to: '/status', label: 'Data status' },
]

/** Persistent strip that answers "where is this data from and how fresh is it" on every page. */
export function ProvenanceStrip({ s }: { s?: Sources }) {
  if (!s) return <div className="h-8 border-t border-rule bg-surface-2" aria-hidden />
  const show = ['nse_bhavcopy', 'nse_cm_market', 'kite_mcp', 'llm', 'web_search']
  return (
    <div
      className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-rule bg-surface-2 px-4 py-1.5 text-xs text-ink-2 lg:px-8"
      aria-label="Data sources and freshness"
    >
      <span>
        Prices to <strong className="font-semibold text-ink">{fmtDate(s.coverage.last_bar)}</strong>
      </span>
      <span>Exchange NSE, currency INR</span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {s.connectors
          .filter((c) => show.includes(c.key))
          .map((c) => {
            // A reachable broker that still needs the account holder's login is not "ok" for data.
            const auth = (c as any).auth?.state
            const state = c.key === 'kite_mcp' && auth === 'login_required' ? 'login_required' : c.state
            const t = stateTone(state)
            return (
              <NavLink
                key={c.key}
                to="/status"
                className="inline-flex items-center gap-1 text-ink-2 no-underline hover:text-ink"
                title={`${c.label}: ${state.replace(/_/g, ' ')}`}
              >
                <span aria-hidden className={t.tone === 'good' ? 'text-gain' : t.tone === 'bad' ? 'text-loss' : t.tone === 'warn' ? 'text-warn' : 'text-ink-3'}>
                  {t.mark}
                </span>
                {c.label.replace(' MCP', '').replace(/ \(.*\)$/, '')}
                <span className="sr-only">: {state.replace(/_/g, ' ')}</span>
              </NavLink>
            )
          })}
      </span>
      <span className="ml-auto">Checked {ago(s.retrieved_at)}</span>
    </div>
  )
}

export function DemoBanner() {
  return (
    <div role="status" className="bg-accent px-4 py-1.5 text-center text-[13px] font-medium text-[#14213d]">
      Demo mode: all prices, financials and transcripts shown are synthetic and for demonstration only. Symbols starting with DEMO are fictional.
    </div>
  )
}

function useThemeToggle() {
  const [mode, setMode] = useState<'light' | 'dark' | null>(() => {
    try {
      return (localStorage.getItem('theme') as 'light' | 'dark' | null) ?? null
    } catch {
      return null
    }
  })
  useEffect(() => {
    if (mode) document.documentElement.dataset.theme = mode
    else delete document.documentElement.dataset.theme
    try {
      if (mode) localStorage.setItem('theme', mode)
      else localStorage.removeItem('theme')
    } catch {
      /* storage unavailable */
    }
  }, [mode])
  const dark = mode ? mode === 'dark' : typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches
  return { dark, toggle: () => setMode(dark ? 'light' : 'dark') }
}

const GROUPS: Array<{ label: string; items: typeof NAV }> = [
  { label: 'Markets', items: NAV.slice(0, 4) },
  { label: 'Research', items: NAV.slice(4, 6) },
  { label: 'You', items: NAV.slice(6) },
]

export function Layout() {
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const theme = useThemeToggle()
  const src = useApi<Sources>(['sources'], '/sources/status', undefined, {
    schema: SourcesSchema as any,
    refetchInterval: 60_000,
    retry: 1,
  })
  return (
    <BrokerLoginProvider enabled={Boolean(src.data)} auth={(src.data?.connectors.find((c: any) => c.key === 'kite_mcp') as any)?.auth ?? null}>
      <div className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        {src.data?.is_demo && <DemoBanner />}
        <div className="lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
          <aside className="bg-rail text-rail-ink lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
            <div className="flex items-center justify-between px-5 py-4">
              <NavLink to="/" className="flex items-center gap-2.5 text-white no-underline">
                <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
                  <rect width="32" height="32" rx="8" fill="var(--rail-2)" />
                  <g fill="var(--accent)">
                    <rect x="6" y="17" width="3" height="9" rx="1" />
                    <rect x="11" y="12" width="3" height="14" rx="1" />
                    <rect x="16" y="15" width="3" height="11" rx="1" />
                    <rect x="21" y="7" width="3" height="19" rx="1" />
                  </g>
                </svg>
                <span className="leading-tight">
                  <span className="block font-display text-[17px] font-semibold">Markets Intelligence</span>
                  <span className="block text-[11.5px] text-rail-ink-2">NSE research and screening</span>
                </span>
              </NavLink>
              <button
                className="rounded-md border border-rail-2 px-2.5 py-1.5 text-sm text-rail-ink lg:hidden"
                aria-expanded={open}
                aria-controls="primary-nav"
                onClick={() => setOpen((o) => !o)}
              >
                Menu
              </button>
            </div>
            <nav id="primary-nav" aria-label="Primary" className={cx('px-3 pb-4 lg:flex lg:flex-1 lg:flex-col', open ? 'block' : 'hidden')}>
              {GROUPS.map((g) => (
                <div key={g.label} className="mb-3">
                  <p className="mb-1 px-3 text-[11.5px] font-medium text-rail-ink-2">{g.label}</p>
                  <ul className="m-0 list-none space-y-0.5 p-0">
                    {g.items.map((n) => (
                      <li key={n.to}>
                        <NavLink
                          to={n.to}
                          end={n.end}
                          onClick={() => setOpen(false)}
                          className={({ isActive }) =>
                            cx(
                              'relative block rounded-[7px] px-3 py-[7px] text-[14px] no-underline transition-colors',
                              isActive
                                ? 'bg-rail-2 font-semibold text-white before:absolute before:bottom-1.5 before:left-0 before:top-1.5 before:w-[3px] before:rounded-full before:bg-accent'
                                : 'text-rail-ink hover:bg-rail-2/60 hover:text-white',
                            )
                          }
                        >
                          {n.label}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              <div className="mt-auto space-y-3 px-3 pt-4">
                <button
                  onClick={theme.toggle}
                  className="flex w-full items-center justify-between rounded-[7px] border border-rail-2 px-3 py-2 text-[13px] text-rail-ink hover:text-white"
                  aria-label={`Switch to ${theme.dark ? 'light' : 'dark'} theme`}
                >
                  <span>{theme.dark ? 'Dark theme' : 'Light theme'}</span>
                  <span aria-hidden>{theme.dark ? '☾' : '☀'}</span>
                </button>
                <p className="text-[11.5px] leading-relaxed text-rail-ink-2">
                  Research and screening only. This tool cannot place orders and its output is not investment advice.
                </p>
              </div>
            </nav>
          </aside>
          <div className="min-w-0">
            <div className="sticky top-0 z-20 border-b border-rule bg-surface/95 backdrop-blur">
              <div className="flex items-center gap-3 px-4 py-2.5 lg:px-8">
                <div className="w-full max-w-lg">
                  <InstrumentSearch
                    onPick={(i) => nav(`/instrument/${i.exchange}:${encodeURIComponent(i.symbol)}`)}
                    placeholder="Find an instrument by symbol or name"
                  />
                </div>
                <span className="ml-auto" />
                <BrokerConnectButton />
                {src.data && (
                  <span
                    className={cx(
                      'hidden whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium md:inline',
                      src.data.is_demo ? 'bg-accent-soft text-ink' : 'bg-good-soft text-gain',
                    )}
                  >
                    {src.data.is_demo ? 'Demo data' : 'Live data'}
                  </span>
                )}
              </div>
              <ProvenanceStrip s={src.data} />
            </div>
            <main id="main" className="mx-auto max-w-[1440px] px-4 py-6 lg:px-8">
              <Outlet />
            </main>
          </div>
        </div>
      </div>
    </BrokerLoginProvider>
  )
}
